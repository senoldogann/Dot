//! The installer's tests (item 575). No test touches this Mac's launchd, Dot home, log folder or network: each makes a
//! folder of its own with stand-in programs for `uv` and `dotx`, and the daemon's answer is a function the test gives.

use super::*;
use std::time::Duration;
use tauri::ipc::{CallbackFn, InvokeBody, InvokeResponseBody};
use tauri::test::{get_ipc_response, MockRuntime, INVOKE_KEY};
use tauri::webview::InvokeRequest;
use tauri::{WebviewUrl, WebviewWindow, WebviewWindowBuilder};

const KEY: &str = "sk-MARKER-0123456789abcdef";
const PLIST: &str = "Library/LaunchAgents/dev.dotagent.dot.plist";

/// A folder of the system's temporary folder for one test, removed with what it holds when the test ends.
struct Scratch(PathBuf);

impl Scratch {
    fn new(test: &str) -> Scratch {
        let path =
            std::env::temp_dir().join(format!("dot-installer-{}-{test}", std::process::id()));
        if let Err(error) = fs::remove_dir_all(&path) {
            assert_eq!(error.kind(), io::ErrorKind::NotFound, "{error}");
        }
        fs::create_dir_all(&path).expect("a folder in the temporary folder");
        Scratch(path)
    }

    fn path(&self, relative: &str) -> PathBuf {
        self.0.join(relative)
    }

    fn write(&self, relative: &str, text: &str) -> PathBuf {
        let path = self.path(relative);
        fs::create_dir_all(path.parent().expect("a file has a folder")).expect("its folder");
        fs::write(&path, text).expect("a file of the test");
        path
    }

    fn script(&self, relative: &str, text: &str) -> PathBuf {
        let path = self.write(relative, text);
        fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).expect("an executable file");
        path
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

/// `uv --no-config tool install [--reinstall] --python 3.13 dotagent-macos==X`, as a stand-in: it makes the Python of
/// the tool environment (a stand-in for the package's command, below) and the metadata folder that names the version,
/// and fails when the test asks it to.
const FAKE_UV: &str = r#"#!/bin/sh
root=$(cd "$(dirname "$0")/.." && pwd)
echo "uv $*" >> "$root/calls.log"
if [ -e "$root/fail-uv" ]; then
  echo "error: No solution found when resolving dependencies" >&2
  exit 1
fi
for last; do :; done
version=${last#dotagent-macos==}
env="$UV_TOOL_DIR/dotagent-macos"
site="$env/lib/python3.13/site-packages"
mkdir -p "$env/bin" "$site"
rmdir "$site"/dotagent_macos-*.dist-info 2>/dev/null
mkdir "$site/dotagent_macos-$version.dist-info"
cp "$root/fake-python" "$env/bin/python"
chmod 755 "$env/bin/python"
echo "Installed 1 executable: dotx"
"#;

/// `python -P -m dotagent setup`, `service install` and `service restart`, as a stand-in: it records what it was run
/// with and the standard input it was given, writes what the real ones write (the config, the token, the job's
/// property list: the label, and the program arguments the real one has, Dot.app's program and this Python), and
/// `setup` fails, with the key in what it prints, when the test asks
/// it to. `service install` fails when the test asks: `fail-service-early` before it made anything (launchctl refused
/// the job), `fail-service-late` after the job is loaded (the daemon did not answer within the minute the real one
/// waits for it).
const FAKE_PYTHON: &str = r#"#!/bin/sh
home=$(cd "$(dirname "$0")/../../../.." && pwd)
root=$(dirname "$home")
echo "python $*" >> "$root/calls.log"
if [ "$1 $2 $3" != "-P -m dotagent" ]; then
  echo "not the package's command: $*" >&2
  exit 64
fi
shift 3
case "$1 $2" in
  "setup --model")
    case " $* " in *" --key-stdin "*) IFS= read -r key; echo "$key" > "$root/stdin.seen";; esac
    if [ -e "$root/fail-setup" ]; then
      echo "error: the provider refused the key $key" >&2
      echo "the key in stdout: $key"
      exit 2
    fi
    echo "model chosen"
    : > "$home/config.toml";;
  "service install")
    if [ -e "$root/fail-service-early" ]; then
      echo "error: launchctl refused the job" >&2
      exit 1
    fi
    mkdir -p "$root/user/Library/LaunchAgents"
    cat > "$root/user/Library/LaunchAgents/dev.dotagent.dot.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
<key>Label</key><string>dev.dotagent.dot</string>
<key>ProgramArguments</key><array>
<string>$home/Dot.app/Contents/MacOS/Dot</string>
<string>$home/runtime/uv-tools/dotagent-macos/bin/python</string>
<string>-P</string><string>-m</string><string>dotagent</string><string>daemon</string>
</array>
</dict></plist>
EOF
    : > "$home/api-token"
    if [ -e "$root/fail-service-late" ]; then
      echo "error: the launchd job is loaded but the daemon does not answer" >&2
      exit 1
    fi;;
  "service restart") ;;
esac
"#;

/// An app's tools, a Dot home and a user's home made of stand-ins, in a folder of their own.
struct Fixture {
    scratch: Scratch,
    ctx: Context,
}

impl Fixture {
    fn new(test: &str) -> Fixture {
        let scratch = Scratch::new(test);
        scratch.script("tools/uv", FAKE_UV);
        scratch.script("tools/rg", "#!/bin/sh\nexit 0\n");
        scratch.script("tools/dot-launcher", "#!/bin/sh\nexit 0\n");
        scratch.write("fake-python", FAKE_PYTHON);
        for folder in ["home", "user", "logs"] {
            fs::create_dir_all(scratch.path(folder)).expect("a folder of the test");
        }
        let ctx = Context {
            tools: Tools {
                dir: scratch.path("tools"),
            },
            home: scratch.path("home"),
            user_home: scratch.path("user"),
            version: "0.1.0".to_string(),
            log_dir: scratch.path("logs"),
            app: None,
            // The daemon answers once the job is installed: the stand-in `dotx` writes the property list.
            answers: |home| {
                home.parent()
                    .is_some_and(|root| root.join("user").join(PLIST).exists())
            },
            daemon_wait: Duration::from_secs(5),
        };
        Fixture { scratch, ctx }
    }

    fn calls(&self) -> Vec<String> {
        fs::read_to_string(self.scratch.path("calls.log"))
            .unwrap_or_default()
            .lines()
            .map(str::to_string)
            .collect()
    }

    fn marker(&self) -> Option<Marker> {
        read_marker(&self.ctx.runtime())
    }

    /// Run the steps once for `choice`, as a begin or a try again does, asking `refusal` before each step.
    fn run(&self, shared: &Shared, refusal: &dyn Fn() -> Option<Reason>) {
        let log = Log::open(&self.ctx.log_dir);
        let answered = || (self.ctx.answers)(&self.ctx.home);
        run_steps(&self.ctx, shared, &log, refusal, &answered);
    }

    /// What the installer asks before each step: no other Dot is here.
    fn nothing_else_is_here(&self) -> Option<Reason> {
        foreign(&self.ctx.found())
    }
}

fn choice(provider: &str, model: &str, key: Option<&str>) -> Choice {
    Choice {
        provider: provider.to_string(),
        model: model.to_string(),
        key: key.map(|key| Secret(key.to_string())),
    }
}

fn shared_for(choice: Choice) -> Shared {
    let shared = Shared {
        inner: Mutex::new(Inner::new()),
        running: AtomicBool::new(false),
        cancel: AtomicBool::new(false),
    };
    shared.with(|inner| inner.choice = Some(choice));
    shared
}

fn no_marker_no_dot() -> Found {
    Found {
        marker: None,
        job: Job::None,
        daemon_answers: false,
        config: false,
        token: false,
        package: false,
    }
}

/// A marker an older build wrote: no steps (it said only whether the daemon had answered, which is not read).
fn without_steps() -> Option<Marker> {
    Some(Marker {
        version: "0.1.0".to_string(),
        steps: None,
    })
}

/// A marker that records the steps of the install that finished.
fn recorded(package: bool, setup: bool, service: bool) -> Option<Marker> {
    Some(Marker {
        version: "0.1.0".to_string(),
        steps: Some(Steps {
            package,
            setup,
            service,
        }),
    })
}

/// The marker of an install whose three steps finished.
fn finished() -> Option<Marker> {
    recorded(true, true, true)
}

#[test]
fn only_an_install_that_stopped_before_the_end_is_resumed() {
    // The page says it goes on from where it stopped: no marker is no install of the app's, and a finished one is not
    // one to resume.
    let own = |package, setup, service| Found {
        marker: recorded(package, setup, service),
        package: true,
        ..no_marker_no_dot()
    };
    assert!(!resumes(&no_marker_no_dot()));
    assert!(!resumes(&own(true, true, true)));
    assert!(resumes(&own(true, false, false)));
    assert!(resumes(&own(true, true, false)));
}

#[test]
fn a_mac_with_no_dot_gets_the_installer_and_one_with_any_of_it_gets_the_window_it_always_had() {
    assert_eq!(decide(&no_marker_no_dot()), Decision::Installer);
    for (found, reason) in [
        (
            Found {
                daemon_answers: true,
                ..no_marker_no_dot()
            },
            Reason::DaemonAnswers,
        ),
        (
            Found {
                job: Job::Other,
                ..no_marker_no_dot()
            },
            Reason::ServiceInstalled,
        ),
        (
            Found {
                config: true,
                ..no_marker_no_dot()
            },
            Reason::Config,
        ),
        (
            Found {
                token: true,
                ..no_marker_no_dot()
            },
            Reason::Token,
        ),
        (
            Found {
                marker: finished(),
                package: true,
                ..no_marker_no_dot()
            },
            Reason::InstallComplete,
        ),
    ] {
        assert_eq!(blocker(&found), Some(reason), "{found:?}");
        assert_eq!(decide(&found), Decision::Dot, "{found:?}");
    }
}

#[test]
fn an_install_of_the_apps_own_that_stopped_is_resumed_unless_another_dot_is_there() {
    let stopped = Found {
        marker: recorded(true, false, false),
        package: true,
        ..no_marker_no_dot()
    };
    assert_eq!(decide(&stopped), Decision::Installer);
    // What its own steps made adds to what the marker recorded: the config of `dotx setup` is the models done, the
    // token and the job of `dotx service install` the service. With the models only, the installer goes on at the
    // service; with the job, the install is complete, and it is no stopped install any longer.
    let after_setup = Found {
        config: true,
        token: true,
        ..stopped.clone()
    };
    assert_eq!(decide(&after_setup), Decision::Installer);
    let own_files = Found {
        job: Job::Ours,
        ..after_setup.clone()
    };
    assert_eq!(decide(&own_files), Decision::Dot);
    assert_eq!(blocker(&own_files), Some(Reason::InstallComplete));
    // A job that names another program, or a daemon that answers, is another Dot's, marker or not.
    for other in [
        Found {
            job: Job::Other,
            ..stopped.clone()
        },
        Found {
            daemon_answers: true,
            ..stopped.clone()
        },
    ] {
        assert_eq!(decide(&other), Decision::Dot, "{other:?}");
    }
}

#[test]
fn what_is_found_is_read_from_dots_home_and_the_launchd_agents_folder() {
    let scratch = Scratch::new("found");
    let (home, user) = (scratch.path("home"), scratch.path("user"));
    fs::create_dir_all(&home).expect("a home");
    assert_eq!(found(&home, &user, false), no_marker_no_dot());
    assert!(found(&home, &user, true).daemon_answers);
    scratch.write("home/config.toml", "");
    scratch.write("home/api-token", "token");
    let seen = found(&home, &user, false);
    assert!(seen.config && seen.token);
    // The job of the app's own (`job_text`) is the app's; any other property list is another Dot's.
    let runtime = Runtime::of(&home);
    scratch.write(&format!("user/{PLIST}"), &job_text(&home));
    assert_eq!(found(&home, &user, false).job, Job::Ours);
    scratch.write(
        &format!("user/{PLIST}"),
        "<string>/Users/someone/Dot/.venv/bin/python</string>",
    );
    assert_eq!(found(&home, &user, false).job, Job::Other);
    // The marker is what the app wrote; a file that is not one is no marker.
    // The package is installed once its Python is in the runtime folder.
    assert!(!found(&home, &user, false).package);
    scratch.script(
        "home/runtime/uv-tools/dotagent-macos/bin/python",
        "#!/bin/sh\n",
    );
    assert!(found(&home, &user, false).package);
    write_marker(
        &runtime,
        &Marker {
            version: "0.1.0".into(),
            steps: None,
        },
    )
    .expect("a marker");
    assert_eq!(found(&home, &user, false).marker, without_steps());
    scratch.write("home/runtime/app-install.json", "not json");
    assert_eq!(found(&home, &user, false).marker, None);
}

#[test]
fn the_package_is_older_when_its_version_reads_lower() {
    for (installed, app) in [
        ("0.1.0", "0.1.1"),
        ("0.9.0", "0.10.0"),
        ("0.9.9", "1.0.0"),
        ("0.2.0rc1", "0.2.0"),
        ("0.2.0.dev3", "0.2.0"),
    ] {
        assert!(older(installed, app), "{installed} < {app}");
        assert!(
            !older(app, installed),
            "{app} is not older than {installed}"
        );
    }
    for (installed, app) in [
        ("0.1.0", "0.1.0"),
        ("0.2.0", "0.2.0rc1"),
        ("1.0", "1.0.1"),
        ("x", "0.1.0"),
        ("0.1.0", "x"),
    ] {
        assert!(
            !older(installed, app),
            "{installed} is not older than {app}"
        );
    }
}

#[test]
fn the_installed_version_is_read_from_the_metadata_folder_without_starting_python() {
    let scratch = Scratch::new("version");
    let runtime = Runtime::of(&scratch.path("home"));
    assert_eq!(installed_version(&runtime), None);
    fs::create_dir_all(
        runtime
            .tool_dir()
            .join(PACKAGE)
            .join("lib/python3.13/site-packages/dotagent_macos-0.3.1.dist-info"),
    )
    .expect("the metadata folder");
    fs::create_dir_all(
        runtime
            .tool_dir()
            .join(PACKAGE)
            .join("lib/python3.13/site-packages/pydantic-2.0.dist-info"),
    )
    .expect("another package's");
    assert_eq!(installed_version(&runtime).as_deref(), Some("0.3.1"));
}

fn tools_at(dir: &str) -> Tools {
    Tools {
        dir: PathBuf::from(dir),
    }
}

fn words(plan: &Plan) -> Vec<String> {
    plan.args
        .iter()
        .map(|arg| arg.to_string_lossy().into_owned())
        .collect()
}

#[test]
fn the_commands_are_built_with_fixed_words_and_the_apps_own_version() {
    let tools = tools_at("/Applications/Dot.app/Contents/Resources/tools");
    let runtime = Runtime::of(Path::new("/Users/me/Library/Application Support/Dot"));
    let install = uv_install(&tools, &runtime, "0.1.0", false);
    assert_eq!(install.program, tools.uv());
    assert_eq!(
        words(&install),
        [
            "--no-config",
            "tool",
            "install",
            "--python",
            "3.13",
            "dotagent-macos==0.1.0"
        ]
    );
    let upgrade = uv_install(&tools, &runtime, "0.2.0", true);
    assert_eq!(
        words(&upgrade),
        [
            "--no-config",
            "tool",
            "install",
            "--reinstall",
            "--python",
            "3.13",
            "dotagent-macos==0.2.0"
        ]
    );
    // Dot's own commands are run by the Python of the package's environment, as the launchd job runs the daemon.
    let service = dotx_service(&tools, &runtime);
    assert_eq!(service.program, runtime.python());
    assert_eq!(
        words(&service),
        [
            "-P",
            "-m",
            "dotagent",
            "service",
            "install",
            "--launcher",
            "/Applications/Dot.app/Contents/Resources/tools/dot-launcher"
        ]
    );
    assert_eq!(
        words(&dotx_restart(&tools, &runtime)),
        ["-P", "-m", "dotagent", "service", "restart", "--when-idle"]
    );
    assert_eq!(
        runtime.python(),
        Path::new(
            "/Users/me/Library/Application Support/Dot/runtime/uv-tools/dotagent-macos/bin/python"
        )
    );
}

#[test]
fn the_key_is_the_setup_commands_standard_input_and_in_no_argument_no_environment_and_no_words_of_the_log(
) {
    let tools = tools_at("/t");
    let runtime = Runtime::of(Path::new("/h"));
    let with_key = dotx_setup(&tools, &runtime, &choice("openai", "gpt-5.5", Some(KEY)));
    assert_eq!(
        words(&with_key),
        [
            "-P",
            "-m",
            "dotagent",
            "setup",
            "--model",
            "openai:gpt-5.5",
            "--no-service",
            "--key-stdin"
        ]
    );
    assert_eq!(with_key.stdin.as_ref().map(Secret::expose), Some(KEY));
    assert_eq!(with_key.program, runtime.python());
    for text in words(&with_key)
        .into_iter()
        .chain(
            with_key
                .env
                .iter()
                .flat_map(|(name, value)| [name.clone(), value.clone()]),
        )
        .chain([with_key.words(), format!("{with_key:?}")])
    {
        assert!(!text.contains(KEY), "the key is in {text:?}");
    }
    // A provider whose models need no key reads nothing from standard input.
    let ollama = dotx_setup(
        &tools,
        &runtime,
        &choice("ollama", "minimax-m2.7:cloud", None),
    );
    assert_eq!(
        words(&ollama),
        [
            "-P",
            "-m",
            "dotagent",
            "setup",
            "--model",
            "ollama:minimax-m2.7:cloud",
            "--no-service"
        ]
    );
    assert!(ollama.stdin.is_none());
}

#[test]
fn every_program_runs_with_the_apps_tools_first_on_its_path_and_uvs_folders_in_the_runtime() {
    let tools = tools_at("/Applications/Dot.app/Contents/Resources/tools");
    let runtime = Runtime::of(Path::new("/h"));
    let env: std::collections::HashMap<String, String> =
        child_env(&runtime, &tools).into_iter().collect();
    assert_eq!(
        env["PATH"],
        "/h/runtime/app-tools:/h/runtime/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    );
    assert_eq!(env["UV_TOOL_DIR"], "/h/runtime/uv-tools");
    assert_eq!(env["UV_TOOL_BIN_DIR"], "/h/runtime/bin");
    assert_eq!(env["UV_PYTHON_INSTALL_DIR"], "/h/runtime/python");
    assert_eq!(env["UV_CACHE_DIR"], "/h/runtime/cache");
    assert_eq!(env["UV_PYTHON_PREFERENCE"], "only-managed");
    assert_eq!(env["PYTHONUNBUFFERED"], "1");
    assert_eq!(
        env["DOT_APP_LAUNCHER"],
        "/Applications/Dot.app/Contents/Resources/tools/dot-launcher"
    );
    assert_eq!(env["DOT_APP_TOOLS"], "/h/runtime/app-tools");
}

fn pairs(names: &[(&str, &str)]) -> Vec<(OsString, OsString)> {
    names
        .iter()
        .map(|(name, value)| (OsString::from(name), OsString::from(value)))
        .collect()
}

fn names_of(environment: Vec<(OsString, OsString)>) -> Vec<String> {
    let mut names: Vec<String> = environment
        .into_iter()
        .map(|(name, _)| name.to_string_lossy().into_owned())
        .collect();
    names.sort();
    names
}

#[test]
fn a_program_inherits_a_short_list_of_the_apps_environment_and_nothing_else() {
    let tools = tools_at("/t");
    let runtime = Runtime::of(Path::new("/h"));
    let plan = uv_install(&tools, &runtime, "0.1.0", false);
    let inherited = pairs(&[
        // What a person's session or a program of theirs could have set, which the step does not ask for.
        ("__PYVENV_LAUNCHER__", "/tmp/planted-python"),
        ("UV_INDEX_URL", "http://127.0.0.1:9/simple"),
        ("UV_PYTHON", "/tmp/planted-python"),
        ("PYTHONPATH", "/tmp/planted"),
        ("PYTHONHOME", "/tmp/planted"),
        ("VIRTUAL_ENV", "/tmp/planted"),
        ("NODE_OPTIONS", "--require /tmp/planted.js"),
        ("NETRC", "/tmp/planted-netrc"),
        ("DYLD_INSERT_LIBRARIES", "/tmp/planted.dylib"),
        ("DOT_APP_LAUNCHER", "/tmp/planted-launcher"),
        ("DOT_APP_TOOLS", "/tmp/planted-tools"),
        ("PATH", "/opt/homebrew/bin:/tmp/planted"),
        ("SHELL", "/bin/zsh"),
        // What the program needs of the app's environment.
        ("HOME", "/Users/me"),
        ("USER", "me"),
        ("LOGNAME", "me"),
        ("TMPDIR", "/var/folders/x/T/"),
        ("LANG", "en_US.UTF-8"),
        ("LC_ALL", "en_US.UTF-8"),
        ("LC_CTYPE", "UTF-8"),
        ("DOT_HOME", "/tmp/dot-home"),
        ("DOT_LOG_DIR", "/tmp/dot-logs"),
        ("DOT_CHROME_DIR", "/tmp/dot-chrome"),
        // Behind a proxy or a company's certificate, uv needs these, in both cases as programs read them.
        ("SSL_CERT_FILE", "/etc/company-ca.pem"),
        ("SSL_CERT_DIR", "/etc/company-certs"),
        ("HTTPS_PROXY", "http://proxy:3128"),
        ("https_proxy", "http://proxy:3128"),
        ("HTTP_PROXY", "http://proxy:3128"),
        ("http_proxy", "http://proxy:3128"),
        ("NO_PROXY", "localhost"),
        ("no_proxy", "localhost"),
        ("ALL_PROXY", "socks5://proxy:1080"),
        ("all_proxy", "socks5://proxy:1080"),
    ]);
    let environment = plan.environment(inherited);
    let value = |name: &str| -> Option<String> {
        environment
            .iter()
            .find(|(have, _)| have == name)
            .map(|(_, value)| value.to_string_lossy().into_owned())
    };
    for kept in [
        "HTTPS_PROXY",
        "https_proxy",
        "SSL_CERT_FILE",
        "HOME",
        "DOT_HOME",
        "LANG",
        "ALL_PROXY",
    ] {
        assert!(value(kept).is_some(), "{kept} does not reach the program");
    }
    assert_eq!(value("HTTPS_PROXY").as_deref(), Some("http://proxy:3128"));
    for dropped in [
        "__PYVENV_LAUNCHER__",
        "UV_INDEX_URL",
        "UV_PYTHON",
        "PYTHONPATH",
        "PYTHONHOME",
        "VIRTUAL_ENV",
        "NODE_OPTIONS",
        "NETRC",
        "DYLD_INSERT_LIBRARIES",
        "SHELL",
    ] {
        assert!(value(dropped).is_none(), "{dropped} reaches the program");
    }
    // What the plan sets wins over what is inherited, and nothing is there twice.
    assert_eq!(
        value("PATH").as_deref(),
        Some("/h/runtime/app-tools:/h/runtime/bin:/usr/bin:/bin:/usr/sbin:/sbin")
    );
    assert_eq!(
        value("DOT_APP_TOOLS").as_deref(),
        Some("/h/runtime/app-tools")
    );
    assert_eq!(
        value("DOT_APP_LAUNCHER").as_deref(),
        Some("/t/dot-launcher")
    );
    let names = names_of(environment);
    let mut once = names.clone();
    once.dedup();
    assert_eq!(names, once);
}

#[test]
fn a_program_is_started_with_nothing_but_that_environment() {
    let scratch = Scratch::new("run-allow-list");
    let program = scratch.script(
        "show-names",
        "#!/bin/sh\n/usr/bin/env | /usr/bin/cut -d= -f1 | /usr/bin/sort\n",
    );
    let log = Log::open(&scratch.path("logs"));
    let plan = Plan {
        program,
        args: vec![],
        env: vec![("UV_TOOL_DIR".to_string(), "/somewhere".to_string())],
        stdin: None,
        limit: Duration::from_secs(20),
    };
    let ran = run(&plan, &scratch.0, &log, &AtomicBool::new(false), |_| {});
    assert_eq!(ran.ended, Ended::Exited(0));
    // What the program sees is what `environment` says, whatever this process has (a shell may add `PWD`, `SHLVL`
    // and `_` of its own).
    let expected = names_of(plan.environment(std::env::vars_os()));
    for name in &ran.lines {
        assert!(
            expected.contains(name) || matches!(name.as_str(), "PWD" | "SHLVL" | "_" | "OLDPWD"),
            "{name} reached the program"
        );
    }
    for name in expected.iter().filter(|name| !name.is_empty()) {
        assert!(ran.lines.contains(name), "{name} did not reach the program");
    }
}

fn input(provider: &str, model: &str, key: Option<&str>) -> ChoiceInput {
    ChoiceInput {
        provider: provider.to_string(),
        model: model.to_string(),
        key: key.map(|key| Secret(key.to_string())),
    }
}

#[test]
fn the_page_chooses_a_provider_a_model_and_a_key_and_nothing_else() {
    let providers = providers();
    assert_eq!(
        providers
            .iter()
            .map(|p| p.provider.as_str())
            .collect::<Vec<_>>(),
        ["ollama", "openai", "anthropic", "opencode_go", "openrouter"]
    );
    let taken = Choice::check(
        input("openai", " gpt-5.5 ", Some(&format!(" {KEY}\n"))),
        &providers,
    )
    .expect("a provider, a model and a key");
    assert_eq!(
        (
            taken.model_ref().as_str(),
            taken.key.as_ref().map(Secret::expose)
        ),
        ("openai:gpt-5.5", Some(KEY))
    );
    assert!(Choice::check(input("ollama", "minimax-m2.7:cloud", None), &providers).is_ok());
    assert!(Choice::check(
        input("openrouter", "anthropic/claude-sonnet-5.5", Some(KEY)),
        &providers
    )
    .is_ok());
    for refused in [
        input("opencode", "big-pickle", None),
        input("not-a-provider", "m", None),
        input("openai", "gpt-5.5", None),
        input("ollama", "m", Some(KEY)),
        input("openai", "gpt-5.5", Some("short")),
        input(
            "openai",
            "gpt-5.5",
            Some("has a space in it, which no key has"),
        ),
        input("openai", "-flag", Some(KEY)),
        input("openai", "--no-service", Some(KEY)),
        input("openai", "", Some(KEY)),
        input("openai", "a b", Some(KEY)),
        input("openai", "a;b", Some(KEY)),
        input("openai", "a\nb", Some(KEY)),
        input("openai", &"m".repeat(MODEL_MAX + 1), Some(KEY)),
    ] {
        assert!(Choice::check(refused, &providers).is_err());
    }
}

#[test]
fn the_secret_shows_nothing_and_a_line_that_holds_it_is_masked() {
    let secret = Secret(KEY.to_string());
    assert_eq!(format!("{secret:?}"), "Secret(..)");
    assert!(!format!("{:?}", input("openai", "gpt-5.5", Some(KEY))).contains(KEY));
    assert!(!format!("{:?}", choice("openai", "gpt-5.5", Some(KEY))).contains(KEY));
    assert_eq!(
        mask(&format!("bad key {KEY}!"), &[KEY]),
        format!("bad key {MASK}!")
    );
    let encoded: String = url::form_urlencoded::byte_serialize(b"sk/a+b=c-0123456789").collect();
    assert_eq!(
        mask(
            &format!("https://x/?key={encoded}"),
            &["sk/a+b=c-0123456789"]
        ),
        format!("https://x/?key={MASK}")
    );
    // A secret too short to be a key would mask words.
    assert_eq!(mask("the key", &["the"]), "the key");
}

#[test]
fn a_program_is_given_the_key_on_its_input_and_what_it_prints_is_masked_before_it_is_kept() {
    let scratch = Scratch::new("run-key");
    let program = scratch.script(
        "echo-key",
        "#!/bin/sh\nIFS= read -r key\necho \"got $key\"\necho \"err $key\" >&2\nexit 3\n",
    );
    let log = Log::open(&scratch.path("logs"));
    let plan = Plan {
        program,
        args: vec![],
        env: vec![],
        stdin: Some(Secret(KEY.to_string())),
        limit: Duration::from_secs(20),
    };
    let mut seen = Vec::new();
    let ran = run(&plan, &scratch.0, &log, &AtomicBool::new(false), |line| {
        seen.push(line.to_string())
    });
    assert_eq!(ran.ended, Ended::Exited(3));
    let mut lines = ran.lines.clone();
    lines.sort();
    assert_eq!(lines, [format!("err {MASK}"), format!("got {MASK}")]);
    assert_eq!(seen.len(), 2);
    let written = fs::read_to_string(scratch.path("logs/installer.log")).expect("the log");
    assert!(
        written.contains(&format!("got {MASK}")) && !written.contains(KEY),
        "{written}"
    );
}

#[test]
fn a_program_that_runs_too_long_or_whose_install_is_closed_is_ended_and_one_that_is_not_there_is_told(
) {
    let scratch = Scratch::new("run-ends");
    let log = Log::open(&scratch.path("logs"));
    let sleeper = scratch.script("sleeper", "#!/bin/sh\nsleep 30\n");
    let plan = |limit: Duration| Plan {
        program: sleeper.clone(),
        args: vec![],
        env: vec![],
        stdin: None,
        limit,
    };
    let started = Instant::now();
    let slow = run(
        &plan(Duration::from_millis(300)),
        &scratch.0,
        &log,
        &AtomicBool::new(false),
        |_| {},
    );
    assert_eq!(slow.ended, Ended::TimedOut);
    let closed = AtomicBool::new(true);
    assert_eq!(
        run(
            &plan(Duration::from_secs(20)),
            &scratch.0,
            &log,
            &closed,
            |_| {}
        )
        .ended,
        Ended::Cancelled
    );
    assert!(
        started.elapsed() < Duration::from_secs(10),
        "neither waited for the sleep"
    );
    let missing = Plan {
        program: scratch.path("nothing-here"),
        args: vec![],
        env: vec![],
        stdin: None,
        limit: Duration::from_secs(5),
    };
    assert!(matches!(
        run(&missing, &scratch.0, &log, &AtomicBool::new(false), |_| {}).ended,
        Ended::NotStarted(_)
    ));
}

#[test]
fn a_program_gets_the_environment_the_plan_gives_and_a_folder_of_its_own() {
    let scratch = Scratch::new("run-env");
    let program = scratch.script(
        "show",
        "#!/bin/sh\necho \"tool dir $UV_TOOL_DIR\"\npwd\nIFS= read -r line || echo \"no input\"\n",
    );
    let log = Log::open(&scratch.path("logs"));
    let plan = Plan {
        program,
        args: vec![],
        env: vec![("UV_TOOL_DIR".to_string(), "/somewhere/uv-tools".to_string())],
        stdin: None,
        limit: Duration::from_secs(20),
    };
    let ran = run(&plan, &scratch.0, &log, &AtomicBool::new(false), |_| {});
    assert_eq!(ran.ended, Ended::Exited(0));
    assert_eq!(ran.lines[0], "tool dir /somewhere/uv-tools");
    assert_eq!(
        fs::canonicalize(&ran.lines[1]).expect("the folder it ran in"),
        fs::canonicalize(&scratch.0).expect("the folder asked")
    );
    assert_eq!(ran.lines[2], "no input");
}

fn status_text(shared: &Shared) -> String {
    let installer = Installer {
        context: None,
        shared: Arc::new(Shared {
            inner: Mutex::new(Inner::new()),
            running: AtomicBool::new(false),
            cancel: AtomicBool::new(false),
        }),
    };
    let copy = shared.with(|inner| {
        (
            inner.states,
            inner.output.clone(),
            inner.failure.clone(),
            inner.phase,
        )
    });
    installer.shared.with(|inner| {
        (inner.states, inner.output, inner.failure, inner.phase) = copy;
    });
    serde_json::to_string(&installer.status()).expect("the status is JSON")
}

#[test]
fn the_steps_run_in_order_with_the_key_on_standard_input_and_the_install_ends_complete() {
    let fixture = Fixture::new("sequence");
    let shared = shared_for(choice("openai", "gpt-5.5", Some(KEY)));
    fixture.run(&shared, &|| fixture.nothing_else_is_here());
    let status = shared.with(|inner| (inner.phase, inner.states, inner.failure.clone()));
    assert_eq!(status.0, Phase::Done, "{:?}", status.2);
    assert_eq!(status.1, [StepState::Done; 4]);
    let runtime = fixture.ctx.runtime();
    let launcher = fixture.ctx.tools.launcher();
    assert_eq!(
        fixture.calls(),
        [
            "uv --no-config tool install --python 3.13 dotagent-macos==0.1.0".to_string(),
            "python -P -m dotagent setup --model openai:gpt-5.5 --no-service --key-stdin"
                .to_string(),
            format!(
                "python -P -m dotagent service install --launcher {}",
                launcher.display()
            ),
        ]
    );
    assert_eq!(
        fs::read_to_string(fixture.scratch.path("stdin.seen"))
            .expect("what setup was given")
            .trim(),
        KEY
    );
    assert_eq!(fixture.marker(), finished());
    assert_eq!(
        links_of(&runtime.tools_folder()),
        [
            ("rg".to_string(), fixture.ctx.tools.dir.join("rg")),
            ("uv".to_string(), fixture.ctx.tools.dir.join("uv"))
        ]
    );
    // The key is in no line the page is shown, no word of the status, and no line of the log, and it is dropped from
    // the install's memory once the install is done.
    assert!(!status_text(&shared).contains(KEY));
    let log = fs::read_to_string(fixture.scratch.path("logs/installer.log")).expect("the log");
    assert!(
        log.contains("== Set up the models ==")
            && log.contains("model chosen")
            && !log.contains(KEY),
        "{log}"
    );
    assert_eq!(
        shared
            .with(|inner| inner.choice.clone())
            .and_then(|c| c.key),
        None
    );
}

#[test]
fn a_failed_step_says_which_with_what_it_printed_masked_and_try_again_goes_on_from_that_step() {
    let fixture = Fixture::new("resume");
    fixture.scratch.write("fail-setup", "");
    let shared = shared_for(choice("openai", "gpt-5.5", Some(KEY)));
    fixture.run(&shared, &|| fixture.nothing_else_is_here());
    let (phase, failure) = shared.with(|inner| (inner.phase, inner.failure.clone()));
    let failure = failure.expect("the step failed");
    assert_eq!(phase, Phase::Failed);
    assert_eq!(
        (failure.step, failure.title),
        ("models", "Set up the models")
    );
    assert!(
        failure.message.contains("exit code 2"),
        "{}",
        failure.message
    );
    assert!(failure
        .output
        .iter()
        .any(|line| line.contains("the provider refused the key")));
    assert!(failure.output.iter().all(|line| !line.contains(KEY)));
    assert!(!failure.fix.is_empty());
    assert!(!status_text(&shared).contains(KEY));
    // The engine is installed and the marker says so, and no more: the next open shows the installer again, at the
    // models, and the config `setup` did not write is not there.
    assert_eq!(fixture.marker(), recorded(true, false, false));
    assert_eq!(decide(&fixture.ctx.found()), Decision::Installer);
    assert_eq!(
        shared.with(|inner| inner.states),
        [
            StepState::Done,
            StepState::Failed,
            StepState::Waiting,
            StepState::Waiting
        ]
    );
    // Try again: the failed step is waiting again, and the package step is not run a second time.
    fs::remove_file(fixture.scratch.path("fail-setup")).expect("the flag");
    shared.with(|inner| {
        inner.states[MODELS_STEP] = StepState::Waiting;
        inner.failure = None;
    });
    fixture.run(&shared, &|| fixture.nothing_else_is_here());
    assert_eq!(shared.with(|inner| inner.phase), Phase::Done);
    let uv_runs = fixture
        .calls()
        .iter()
        .filter(|call| call.starts_with("uv "))
        .count();
    assert_eq!(uv_runs, 1);
    assert_eq!(fixture.marker(), finished());
}

#[test]
fn a_failed_package_install_leaves_no_marker_and_says_what_pypi_lacks() {
    let fixture = Fixture::new("no-marker");
    fixture.scratch.write("fail-uv", "");
    let shared = shared_for(choice("ollama", "minimax-m2.7:cloud", None));
    fixture.run(&shared, &|| fixture.nothing_else_is_here());
    let failure = shared
        .with(|inner| inner.failure.clone())
        .expect("uv failed");
    assert_eq!(failure.step, "package");
    assert!(
        failure.fix.contains("PyPI does not have Dot 0.1.0 yet"),
        "{}",
        failure.fix
    );
    assert_eq!(fixture.marker(), None);
    assert_eq!(decide(&fixture.ctx.found()), Decision::Installer);
    assert_eq!(fixture.calls().len(), 1, "nothing after uv ran");
}

#[test]
fn a_dot_that_is_there_stops_the_install_before_any_step_runs_over_it() {
    let mut fixture = Fixture::new("over-another");
    // No daemon answers: what is there is the job of a Dot installed elsewhere.
    fixture.ctx.answers = |_| false;
    fixture.scratch.write(
        &format!("user/{PLIST}"),
        "<string>/Users/someone/Dot/.venv/bin/python</string>",
    );
    let shared = shared_for(choice("ollama", "minimax-m2.7:cloud", None));
    fixture.run(&shared, &|| fixture.nothing_else_is_here());
    let failure = shared
        .with(|inner| inner.failure.clone())
        .expect("the install was refused");
    assert!(
        failure.message.contains("service is already installed"),
        "{}",
        failure.message
    );
    assert_eq!(fixture.calls(), Vec::<String>::new());
    assert_eq!(fixture.marker(), None);
    assert!(!fixture.ctx.runtime().dir.join("bin").exists());
}

#[test]
fn a_dot_that_comes_up_between_two_steps_stops_the_install_there() {
    let fixture = Fixture::new("appears");
    let shared = shared_for(choice("ollama", "minimax-m2.7:cloud", None));
    let asked = std::cell::Cell::new(0);
    let refusal = || {
        asked.set(asked.get() + 1);
        (asked.get() > 1).then_some(Reason::DaemonAnswers)
    };
    fixture.run(&shared, &refusal);
    let failure = shared
        .with(|inner| inner.failure.clone())
        .expect("refused before the second step");
    assert_eq!(failure.step, "models");
    assert!(
        failure.message.contains("a Dot daemon already answers"),
        "{}",
        failure.message
    );
    assert_eq!(fixture.calls().len(), 1, "only uv ran");
    assert_eq!(fixture.marker(), recorded(true, false, false));
}

#[test]
fn the_installer_runs_from_a_thread_of_its_own_and_its_status_is_the_pages_whole_view() {
    let fixture = Fixture::new("begin");
    let installer = Installer::new(Some(fixture.ctx.clone()));
    assert_eq!(installer.status().phase, Phase::Idle);
    assert!(
        installer.begin(None).is_err(),
        "there is nothing to try again before a start"
    );
    installer
        .begin(Some(choice("openai", "gpt-5.5", Some(KEY))))
        .expect("the install starts");
    let deadline = Instant::now() + Duration::from_secs(30);
    while installer.status().phase == Phase::Running {
        assert!(Instant::now() < deadline, "the install did not end");
        thread::sleep(Duration::from_millis(50));
    }
    let status = installer.status();
    assert_eq!(status.phase, Phase::Done, "{:?}", status.failure);
    assert_eq!(
        status
            .steps
            .iter()
            .map(|step| (step.id, step.state))
            .collect::<Vec<_>>(),
        [
            ("package", StepState::Done),
            ("models", StepState::Done),
            ("service", StepState::Done),
            ("daemon", StepState::Done)
        ]
    );
    assert!(status.steps[0]
        .output
        .iter()
        .any(|line| line.starts_with("Installed 1 executable")));
    assert!(!serde_json::to_string(&status).expect("JSON").contains(KEY));
    assert!(!installer.running());
    // Once Dot is installed the installer will not begin again, over its own install either.
    assert!(installer.begin(None).is_err());
}

#[test]
fn an_install_is_refused_when_another_dot_is_there_or_the_app_is_not_where_an_app_should_be() {
    let fixture = Fixture::new("refused");
    fixture.scratch.write("home/config.toml", "");
    let installer = Installer::new(Some(fixture.ctx.clone()));
    let refused = installer
        .begin(Some(choice("ollama", "minimax-m2.7:cloud", None)))
        .expect_err("a config.toml is there");
    assert!(refused.contains("config.toml"), "{refused}");
    assert_eq!(installer.status().phase, Phase::Idle);
    assert_eq!(fixture.calls(), Vec::<String>::new());
    let mut moved = Fixture::new("not-moved");
    moved.ctx.app = Some(PathBuf::from("/Volumes/Dot/Dot.app"));
    let installer = Installer::new(Some(moved.ctx.clone()));
    let refused = installer
        .begin(Some(choice("ollama", "minimax-m2.7:cloud", None)))
        .expect_err("the app runs from its disk image");
    assert!(refused.contains("Applications"), "{refused}");
    assert!(Installer::new(None).begin(None).is_err());
}

#[test]
fn the_app_is_where_an_app_should_be_unless_it_runs_from_its_image_or_a_translocated_copy() {
    assert!(app_location_ok(Path::new("/Applications/Dot.app")));
    assert!(app_location_ok(Path::new("/Users/me/Applications/Dot.app")));
    assert!(!app_location_ok(Path::new("/Volumes/Dot/Dot.app")));
    assert!(!app_location_ok(Path::new(
        "/private/var/folders/xy/T/AppTranslocation/0C1E/d/Dot.app"
    )));
    assert_eq!(
        app_of(Path::new(
            "/Applications/Dot.app/Contents/MacOS/dot-desktop"
        )),
        Some(PathBuf::from("/Applications/Dot.app"))
    );
    assert_eq!(app_of(Path::new("/work/target/debug/dot-desktop")), None);
}

/// What a folder holds, as `(name, where it points)` of each entry, which must all be links, in the order of their names.
fn links_of(folder: &Path) -> Vec<(String, PathBuf)> {
    let mut found: Vec<(String, PathBuf)> = fs::read_dir(folder)
        .expect("the folder")
        .map(|entry| {
            let entry = entry.expect("an entry");
            let name = entry.file_name().to_string_lossy().into_owned();
            let target =
                fs::read_link(entry.path()).unwrap_or_else(|_| PathBuf::from("(not a link)"));
            (name, target)
        })
        .collect();
    found.sort();
    found
}

/// A tools folder as the app carries one, with a file beside the three programs that no release should carry.
fn tools_with_a_stray_file(scratch: &Scratch, folder: &str) -> Tools {
    for name in ["uv", "rg", "dot-launcher", "op"] {
        scratch.script(&format!("{folder}/{name}"), "#!/bin/sh\nexit 0\n");
    }
    Tools {
        dir: scratch.path(folder),
    }
}

#[test]
fn the_path_folder_holds_the_links_of_rg_and_uv_and_nothing_else_made_again_where_the_app_is_now() {
    let scratch = Scratch::new("link");
    let runtime = Runtime::of(&scratch.path("home"));
    let first = tools_with_a_stray_file(&scratch, "one/tools");
    let second = tools_with_a_stray_file(&scratch, "two/tools");
    link_tools(&runtime, &first).expect("the folder is made");
    let expected = |tools: &Tools| {
        vec![
            ("rg".to_string(), tools.dir.join("rg")),
            ("uv".to_string(), tools.dir.join("uv")),
        ]
    };
    // Two links, and not the launcher, not a stray file, and not the whole folder of the app's tools.
    assert_eq!(links_of(&runtime.tools_folder()), expected(&first));
    assert!(fs::symlink_metadata(runtime.tools_folder())
        .expect("the folder")
        .file_type()
        .is_dir());
    // Again at the next start: nothing changes, and a link that moved with the app is replaced.
    link_tools(&runtime, &first).expect("the same again");
    assert_eq!(links_of(&runtime.tools_folder()), expected(&first));
    link_tools(&runtime, &second).expect("the app moved");
    assert_eq!(links_of(&runtime.tools_folder()), expected(&second));
    assert!(!runtime.tools_folder().join(".rg.new").exists());
}

#[test]
fn what_was_put_in_the_path_folder_is_replaced_by_the_two_links() {
    let scratch = Scratch::new("link-planted");
    let runtime = Runtime::of(&scratch.path("home"));
    let tools = tools_with_a_stray_file(&scratch, "tools");
    let folder = runtime.tools_folder();
    fs::create_dir_all(folder.join("bin")).expect("a folder");
    fs::write(folder.join("bin/op"), "planted").expect("a file in it");
    fs::write(folder.join("op"), "planted").expect("a planted program");
    fs::write(folder.join(".hidden"), "planted").expect("a hidden file");
    symlink("/usr/bin/true", folder.join("git")).expect("a planted link");
    symlink("/usr/bin/true", folder.join(".rg.new")).expect("a link left by an earlier start");
    // `rg` is a file of the person's, `uv` a link to somewhere else, and nothing of it stays.
    fs::write(folder.join("rg"), "planted").expect("a file where a link belongs");
    symlink("/tmp/somewhere-else", folder.join("uv")).expect("a link that points elsewhere");
    link_tools(&runtime, &tools).expect("the folder is made again");
    assert_eq!(
        links_of(&folder),
        [
            ("rg".to_string(), tools.dir.join("rg")),
            ("uv".to_string(), tools.dir.join("uv"))
        ]
    );
    // A folder where a link belongs goes too.
    fs::remove_file(folder.join("rg")).expect("the link");
    fs::create_dir(folder.join("rg")).expect("a folder where a link belongs");
    fs::write(folder.join("rg/inside"), "planted").expect("a file in it");
    link_tools(&runtime, &tools).expect("again");
    assert_eq!(links_of(&folder).len(), 2);
    assert_eq!(
        fs::read_link(folder.join("rg")).expect("a link"),
        tools.dir.join("rg")
    );
}

#[test]
fn a_link_where_the_path_folder_belongs_is_replaced_and_nothing_is_written_through_it() {
    let scratch = Scratch::new("link-old");
    let runtime = Runtime::of(&scratch.path("home"));
    let tools = tools_with_a_stray_file(&scratch, "tools");
    let elsewhere = scratch.path("elsewhere");
    fs::create_dir_all(&elsewhere).expect("a folder somewhere else");
    // The first design made one link to the whole tools folder; a link to another folder could be left there too.
    for old in [tools.dir.clone(), elsewhere.clone()] {
        fs::create_dir_all(&runtime.dir).expect("the runtime folder");
        remove_entry(&runtime.tools_folder()).expect("what was there");
        symlink(&old, runtime.tools_folder()).expect("a link where the folder belongs");
        link_tools(&runtime, &tools).expect("the folder is made");
        assert!(fs::symlink_metadata(runtime.tools_folder())
            .expect("the folder")
            .file_type()
            .is_dir());
        assert_eq!(links_of(&runtime.tools_folder()).len(), 2);
    }
    assert_eq!(
        fs::read_dir(&elsewhere).expect("the other folder").count(),
        0,
        "nothing was written through the link"
    );
    assert!(
        !tools.dir.join("rg").is_symlink(),
        "the app's own tools are untouched"
    );
}

#[test]
fn a_line_is_masked_before_it_is_cut_so_that_a_key_across_the_cut_is_not_left_in_part() {
    let scratch = Scratch::new("run-cut");
    // 580 blanks, then the key: cut first, the line would end 20 characters into the key.
    let program = scratch.script(
        "blanks-then-key",
        "#!/bin/sh\nIFS= read -r key\nprintf '%580s%s and more\\n' '' \"$key\"\n",
    );
    let log = Log::open(&scratch.path("logs"));
    let plan = Plan {
        program,
        args: vec![],
        env: vec![],
        stdin: Some(Secret(KEY.to_string())),
        limit: Duration::from_secs(20),
    };
    let ran = run(&plan, &scratch.0, &log, &AtomicBool::new(false), |_| {});
    assert_eq!(ran.ended, Ended::Exited(0));
    assert_eq!(ran.lines.len(), 1);
    let prefix = &KEY[..KEY_MIN];
    assert!(!ran.lines[0].contains(prefix), "{}", ran.lines[0]);
    assert!(ran.lines[0].chars().count() <= LINE_CHARS);
    let written = fs::read_to_string(scratch.path("logs/installer.log")).expect("the log");
    assert!(
        !written.contains(prefix) && written.contains(MASK),
        "{written}"
    );
    // A line that is long and holds no key is cut as before.
    assert_eq!(
        shown(&"x".repeat(LINE_CHARS + 50)).chars().count(),
        LINE_CHARS
    );
}

#[test]
fn the_log_is_not_opened_through_a_link_left_at_its_name() {
    let scratch = Scratch::new("log-link");
    let logs = scratch.path("logs");
    fs::create_dir_all(&logs).expect("the log folder");
    let victim = scratch.write("victim.txt", "the person's own file\n");
    symlink(&victim, logs.join("installer.log")).expect("a link at the log's name");
    let log = Log::open(&logs);
    log.line("a line the victim must not get");
    assert_eq!(
        fs::read_to_string(&victim).expect("the victim"),
        "the person's own file\n"
    );
    // A log of its own is opened and appended to.
    remove_entry(&logs.join("installer.log")).expect("the link goes");
    let log = Log::open(&logs);
    log.line("one");
    log.line("two");
    let written = fs::read_to_string(logs.join("installer.log")).expect("the log");
    assert!(written.contains("one") && written.contains("two"));
    assert_eq!(
        logs.join("installer.log")
            .metadata()
            .expect("the log")
            .permissions()
            .mode()
            & 0o777,
        0o600
    );
}

#[test]
fn the_marker_is_not_written_through_a_link_left_at_its_staging_name() {
    let scratch = Scratch::new("marker-link");
    let runtime = Runtime::of(&scratch.path("home"));
    fs::create_dir_all(&runtime.dir).expect("the runtime folder");
    let victim = scratch.write("victim.txt", "the person's own file\n");
    symlink(&victim, runtime.dir.join(format!(".{MARKER}.tmp")))
        .expect("a link at the staging name");
    let marker = Marker {
        version: "0.1.0".to_string(),
        steps: Some(Steps {
            package: true,
            setup: false,
            service: false,
        }),
    };
    write_marker(&runtime, &marker).expect("the marker is written");
    assert_eq!(
        fs::read_to_string(&victim).expect("the victim"),
        "the person's own file\n"
    );
    assert_eq!(read_marker(&runtime), Some(marker));
}

#[test]
fn a_build_without_the_tools_has_no_installer() {
    let scratch = Scratch::new("bundled");
    assert!(bundled(&scratch.0).is_none());
    scratch.script("tools/uv", "#!/bin/sh\n");
    scratch.script("tools/rg", "#!/bin/sh\n");
    assert!(bundled(&scratch.0).is_none(), "the launcher is missing");
    scratch.write("tools/dot-launcher", "not executable");
    assert!(bundled(&scratch.0).is_none(), "the launcher cannot be run");
    scratch.script("tools/dot-launcher", "#!/bin/sh\n");
    assert!(bundled(&scratch.0).is_some());
}

fn complete_install(fixture: &Fixture, installed: &str) {
    let shared = shared_for(choice("ollama", "minimax-m2.7:cloud", None));
    let mut ctx = fixture.ctx.clone();
    ctx.version = installed.to_string();
    let log = Log::open(&ctx.log_dir);
    let answered = || (ctx.answers)(&ctx.home);
    run_steps(&ctx, &shared, &log, &|| blocker(&ctx.found()), &answered);
    assert_eq!(shared.with(|inner| inner.phase), Phase::Done);
    fs::remove_file(fixture.scratch.path("calls.log")).expect("the calls so far");
}

#[test]
fn an_app_that_updated_itself_upgrades_the_package_it_installed_and_asks_for_a_restart() {
    let fixture = Fixture::new("upgrade");
    complete_install(&fixture, "0.1.0");
    let mut ctx = fixture.ctx.clone();
    ctx.version = "0.2.0".to_string();
    let said = keep_in_step(&ctx)
        .expect("the upgrade works")
        .expect("there was something to do");
    assert!(said.contains("0.2.0"), "{said}");
    assert_eq!(
        fixture.calls(),
        [
            "uv --no-config tool install --reinstall --python 3.13 dotagent-macos==0.2.0",
            "python -P -m dotagent service restart --when-idle"
        ]
    );
    assert_eq!(installed_version(&ctx.runtime()).as_deref(), Some("0.2.0"));
    assert_eq!(
        fixture.marker(),
        Some(Marker {
            version: "0.2.0".to_string(),
            steps: Some(Steps {
                package: true,
                setup: true,
                service: true
            }),
        })
    );
    // At the same version, and with a package newer than the app, nothing is done.
    assert_eq!(keep_in_step(&ctx), Ok(None));
    ctx.version = "0.1.5".to_string();
    assert_eq!(keep_in_step(&ctx), Ok(None));
    assert_eq!(fixture.calls().len(), 2);
}

#[test]
fn only_an_install_the_app_made_and_finished_is_ever_upgraded() {
    // No marker: a checkout, or a Dot installed by hand.
    let fixture = Fixture::new("not-ours");
    let mut ctx = fixture.ctx.clone();
    ctx.version = "9.9.9".to_string();
    fs::create_dir_all(
        ctx.runtime()
            .tool_dir()
            .join(PACKAGE)
            .join("lib/python3.13/site-packages/dotagent_macos-0.1.0.dist-info"),
    )
    .expect("a package that is older");
    assert_eq!(keep_in_step(&ctx), Ok(None));
    // A marker of an install that did not finish: the package only.
    write_marker(
        &ctx.runtime(),
        &Marker {
            version: "0.1.0".into(),
            steps: Some(Steps {
                package: true,
                setup: false,
                service: false,
            }),
        },
    )
    .expect("a marker");
    assert_eq!(keep_in_step(&ctx), Ok(None));
    assert_eq!(fixture.calls(), Vec::<String>::new());
}

#[test]
fn an_upgrade_that_fails_says_so_and_leaves_the_marker_and_the_package_as_they_were() {
    let fixture = Fixture::new("upgrade-fails");
    complete_install(&fixture, "0.1.0");
    fixture.scratch.write("fail-uv", "");
    let mut ctx = fixture.ctx.clone();
    ctx.version = "0.2.0".to_string();
    let words = keep_in_step(&ctx).expect_err("uv failed");
    assert!(words.contains("could not be updated to 0.2.0"), "{words}");
    assert_eq!(installed_version(&ctx.runtime()).as_deref(), Some("0.1.0"));
    assert_eq!(
        fixture.marker().map(|marker| marker.version),
        Some("0.1.0".to_string())
    );
    assert_eq!(fixture.calls().len(), 1, "no restart was asked for");
}

// ----------------------------------------------------- what completes the install, and what the next start does with it

/// What Try again does to the state of the install: a step that failed is waiting again.
fn again(shared: &Shared) {
    shared.with(|inner| {
        for state in &mut inner.states {
            if *state == StepState::Failed {
                *state = StepState::Waiting;
            }
        }
        inner.failure = None;
    });
}

/// The status of the installer once its run is over, after at most half a minute.
fn over(installer: &Installer) -> Status {
    let deadline = Instant::now() + Duration::from_secs(30);
    while installer.status().phase == Phase::Running || installer.running() {
        assert!(Instant::now() < deadline, "the install did not end");
        thread::sleep(Duration::from_millis(50));
    }
    installer.status()
}

/// How many of the calls the stand-ins recorded say `what`.
fn ran(fixture: &Fixture, what: &str) -> usize {
    fixture
        .calls()
        .iter()
        .filter(|call| call.contains(what))
        .count()
}

const ALL: Steps = Steps {
    package: true,
    setup: true,
    service: true,
};

/// An install whose steps ended well but for the last, the wait for the daemon's first answer, which ran out: the app
/// is quit there, as an owner may quit it.
fn install_whose_wait_ran_out(test: &str) -> Fixture {
    let mut fixture = Fixture::new(test);
    fixture.ctx.answers = |_| false;
    fixture.ctx.daemon_wait = Duration::from_millis(200);
    let shared = shared_for(choice("openai", "gpt-5.5", Some(KEY)));
    fixture.run(&shared, &|| fixture.nothing_else_is_here());
    let failure = shared
        .with(|inner| inner.failure.clone())
        .expect("the wait ran out");
    assert_eq!(failure.step, "daemon");
    assert!(
        failure.message.contains("Dot is installed"),
        "{}",
        failure.message
    );
    fixture
}

#[test]
fn the_marker_records_each_step_as_it_finishes() {
    let fixture = Fixture::new("records");
    let shared = shared_for(choice("openai", "gpt-5.5", Some(KEY)));
    // The models step fails: the package is recorded, and nothing more.
    fixture.scratch.write("fail-setup", "");
    fixture.run(&shared, &|| fixture.nothing_else_is_here());
    assert_eq!(fixture.marker(), recorded(true, false, false));
    // The service step fails (launchctl refuses the job): the models are recorded too.
    fs::remove_file(fixture.scratch.path("fail-setup")).expect("the flag");
    fixture.scratch.write("fail-service-early", "");
    again(&shared);
    fixture.run(&shared, &|| fixture.nothing_else_is_here());
    assert_eq!(fixture.marker(), recorded(true, true, false));
    // The service installed: all three, and the daemon has not been waited for yet when the marker says so.
    fs::remove_file(fixture.scratch.path("fail-service-early")).expect("the flag");
    again(&shared);
    fixture.run(&shared, &|| fixture.nothing_else_is_here());
    assert_eq!(fixture.marker(), finished());
}

#[test]
fn the_install_is_complete_when_its_service_is_installed_whether_or_not_the_daemon_has_answered() {
    let fixture = install_whose_wait_ran_out("complete-before-answer");
    assert_eq!(fixture.marker(), finished());
    let found = fixture.ctx.found();
    assert!(!found.daemon_answers);
    assert_eq!(found.done(), Some(ALL));
    assert_eq!(blocker(&found), Some(Reason::InstallComplete));
    assert!(!resumes(&found));
}

#[test]
fn a_wait_that_ran_out_and_an_app_that_was_quit_leave_an_install_that_the_next_start_keeps_in_step()
{
    let fixture = install_whose_wait_ran_out("late-answer");
    // The app was quit; launchd went on, and its daemon answers by the time the app is opened again, in a version that
    // is newer than the package (the app updated itself meanwhile).
    let mut next = fixture.ctx.clone();
    next.answers = |_| true;
    next.version = "0.2.0".to_string();
    assert_eq!(decide(&next.found()), Decision::Dot);
    assert!(!wanted(&next));
    fs::remove_file(fixture.scratch.path("calls.log")).expect("the calls of the install");
    let said = keep_in_step(&next)
        .expect("the upgrade works")
        .expect("there was something to do");
    assert!(said.contains("0.2.0"), "{said}");
    assert_eq!(
        fixture.calls(),
        [
            "uv --no-config tool install --reinstall --python 3.13 dotagent-macos==0.2.0",
            "python -P -m dotagent service restart --when-idle"
        ]
    );
    assert_eq!(installed_version(&next.runtime()).as_deref(), Some("0.2.0"));
    assert_eq!(
        fixture.marker(),
        Some(Marker {
            version: "0.2.0".to_string(),
            steps: Some(ALL),
        })
    );
}

#[test]
fn the_same_start_with_the_daemon_down_is_the_window_dot_always_had_and_never_the_installer() {
    let fixture = install_whose_wait_ran_out("late-answer-down");
    let mut next = fixture.ctx.clone();
    next.answers = |_| false;
    next.version = "0.2.0".to_string();
    let found = next.found();
    // `lib.rs` shows the UI where the daemon answers and the page that says it is down where it does not.
    assert_eq!(decide(&found), Decision::Dot);
    assert!(!wanted(&next));
    assert_eq!(blocker(&found), Some(Reason::InstallComplete));
    assert!(!resumes(&found));
    // Install has nothing to run: the install is complete, with a choice or without.
    let installer = Installer::new(Some(next.clone()));
    for asked in [None, Some(choice("openai", "gpt-5.5", Some(KEY)))] {
        let refused = installer.begin(asked).expect_err("the install is complete");
        assert!(refused.contains("installed already"), "{refused}");
    }
    assert_eq!(installer.status().phase, Phase::Idle);
    // And the package is upgraded all the same, as at any start of an install that is complete.
    fs::remove_file(fixture.scratch.path("calls.log")).expect("the calls of the install");
    assert!(keep_in_step(&next).expect("the upgrade works").is_some());
    assert_eq!(ran(&fixture, "tool install --reinstall"), 1);
    assert_eq!(ran(&fixture, "dotagent setup"), 0);
}

#[test]
fn try_again_after_the_wait_ran_out_waits_once_more_and_nothing_is_run_again() {
    let mut fixture = Fixture::new("wait-again");
    fixture.ctx.answers = |_| false;
    fixture.ctx.daemon_wait = Duration::from_millis(200);
    let installer = Installer::new(Some(fixture.ctx.clone()));
    installer
        .begin(Some(choice("openai", "gpt-5.5", Some(KEY))))
        .expect("the install starts");
    let status = over(&installer);
    assert_eq!(
        (status.phase, status.failure.map(|failure| failure.step)),
        (Phase::Failed, Some("daemon"))
    );
    let calls = fixture.calls();
    // The install is complete, and what is left is the wait: Try again is not refused as Install is on a complete
    // install, and it runs nothing but the wait.
    installer.begin(None).expect("Try again waits once more");
    let status = over(&installer);
    assert_eq!(status.phase, Phase::Failed);
    assert_eq!(fixture.calls(), calls, "nothing was run again");
    assert_eq!(fixture.marker(), finished());
}

#[test]
fn a_service_step_that_loaded_the_job_and_failed_is_tried_again_and_is_the_apps_service_at_the_next_start(
) {
    let mut fixture = Fixture::new("late-failure");
    fixture.ctx.answers = |_| false;
    fixture.scratch.write("fail-service-late", "");
    let installer = Installer::new(Some(fixture.ctx.clone()));
    installer
        .begin(Some(choice("openai", "gpt-5.5", Some(KEY))))
        .expect("the install starts");
    let status = over(&installer);
    let failure = status.failure.expect("the service step failed");
    assert_eq!(failure.step, "service");
    assert!(
        failure
            .output
            .iter()
            .any(|line| line.contains("does not answer")),
        "{:?}",
        failure.output
    );
    // The job is loaded, so the disk shows the service installed, and the marker did not record the step.
    assert_eq!(fixture.marker(), recorded(true, true, false));
    assert_eq!(fixture.ctx.found().job, Job::Ours);
    // Try again in this session is not refused for that: the step is run again.
    fs::remove_file(fixture.scratch.path("fail-service-late")).expect("the flag");
    installer
        .begin(None)
        .expect("Try again runs the service step");
    let status = over(&installer);
    assert_eq!(status.phase, Phase::Failed, "the daemon is still down");
    assert_eq!(status.failure.map(|failure| failure.step), Some("daemon"));
    assert_eq!(ran(&fixture, "service install"), 2);
    assert_eq!(fixture.marker(), finished());
    // An app that was quit after the first failure finds the same: the job is the app's own, so the install is
    // complete and the window is the one Dot always had.
    fixture.scratch.write(
        "home/runtime/app-install.json",
        r#"{"version":"0.1.0","steps":{"package":true,"setup":true}}"#,
    );
    fixture.ctx.answers = |_| false;
    assert_eq!(fixture.ctx.found().done(), Some(ALL));
    assert_eq!(decide(&fixture.ctx.found()), Decision::Dot);
}

#[test]
fn an_install_that_stopped_after_the_models_goes_on_at_the_service_and_never_runs_setup_again() {
    let fixture = Fixture::new("stop-after-setup");
    fixture.scratch.write("fail-service-early", "");
    let shared = shared_for(choice("openai", "gpt-5.5", Some(KEY)));
    fixture.run(&shared, &|| fixture.nothing_else_is_here());
    let failure = shared
        .with(|inner| inner.failure.clone())
        .expect("the service step failed");
    assert_eq!(failure.step, "service");
    assert_eq!(fixture.marker(), recorded(true, true, false));
    assert_eq!(ran(&fixture, "dotagent setup"), 1);
    // The person went on to change the config in the UI's settings: it is theirs now.
    fixture
        .scratch
        .write("home/config.toml", "# the person's own settings\n");
    // The app is quit here and opened again: the install stopped before its end, so the window is the installer, and
    // it knows the models are done...
    let found = fixture.ctx.found();
    assert_eq!(decide(&found), Decision::Installer);
    assert!(resumes(&found));
    assert_eq!(
        found.done(),
        Some(Steps {
            package: true,
            setup: true,
            service: false
        })
    );
    // ...so that Install goes on at the service with nothing to choose, and `dotx setup` is not run over that config.
    // The folder of links to the app's tools, which the models step made, is gone (the app was moved, say): the service
    // step, the first this session runs, makes it again, since the job's `PATH` begins with it.
    fs::remove_dir_all(fixture.ctx.runtime().tools_folder()).expect("the links");
    fs::remove_file(fixture.scratch.path("fail-service-early")).expect("the flag");
    let installer = Installer::new(Some(fixture.ctx.clone()));
    installer
        .begin(None)
        .expect("the install goes on without a choice");
    let status = over(&installer);
    assert_eq!(status.phase, Phase::Done, "{:?}", status.failure);
    assert_eq!(
        status
            .steps
            .iter()
            .map(|step| step.state)
            .collect::<Vec<_>>(),
        [StepState::Done; 4]
    );
    assert_eq!(
        ran(&fixture, "dotagent setup"),
        1,
        "setup was not run again"
    );
    assert_eq!(
        ran(&fixture, "tool install"),
        1,
        "the package was not installed again"
    );
    assert_eq!(
        ran(&fixture, "service install"),
        2,
        "the step that failed, then this one"
    );
    assert_eq!(
        fs::read_to_string(fixture.scratch.path("home/config.toml")).expect("the config"),
        "# the person's own settings\n"
    );
    assert_eq!(
        links_of(&fixture.ctx.runtime().tools_folder()),
        [
            ("rg".to_string(), fixture.ctx.tools.dir.join("rg")),
            ("uv".to_string(), fixture.ctx.tools.dir.join("uv"))
        ]
    );
    assert_eq!(fixture.marker(), finished());
}

#[test]
fn a_choice_sent_after_a_restart_is_refused_in_words_and_never_run_or_dropped_without_a_word() {
    let fixture = Fixture::new("choice-after-restart");
    fixture.scratch.write("fail-service-early", "");
    let shared = shared_for(choice("openai", "gpt-5.5", Some(KEY)));
    fixture.run(&shared, &|| fixture.nothing_else_is_here());
    // The app is opened again: the models are done from the disk, and the person chose none in this session. The page
    // does not ask for them (`nextScreen`), and Change the models is not offered (`can_change_models`); a choice that
    // reaches the shell all the same, from a page that was open before, is refused in words.
    let installer = Installer::new(Some(fixture.ctx.clone()));
    let refused = installer
        .begin(Some(choice("ollama", "minimax-m2.7:cloud", None)))
        .expect_err("a choice cannot change models that were set up before this session");
    assert!(
        refused.contains("set up already") && refused.contains("Settings"),
        "{refused}"
    );
    assert_eq!(installer.status().phase, Phase::Idle);
    assert!(!installer.running());
    assert_eq!(
        ran(&fixture, "dotagent setup"),
        1,
        "setup was not run again"
    );
    assert_eq!(ran(&fixture, "service install"), 1, "nothing was run");
    // Going on without a choice is what is offered, and it works.
    fs::remove_file(fixture.scratch.path("fail-service-early")).expect("the flag");
    installer
        .begin(None)
        .expect("the install goes on without a choice");
    let status = over(&installer);
    assert_eq!(status.phase, Phase::Done, "{:?}", status.failure);
    assert_eq!(ran(&fixture, "dotagent setup"), 1);
    assert_eq!(fixture.marker(), finished());
}

#[test]
fn change_the_models_is_offered_where_the_session_made_a_choice_and_not_where_it_did_not() {
    // A session that chose the models: its service step fails, Change the models is offered, and a new choice is taken
    // (the models are set up again with it).
    let fixture = Fixture::new("change-the-models");
    fixture.scratch.write("fail-service-early", "");
    let installer = Installer::new(Some(fixture.ctx.clone()));
    assert!(installer.status().can_change_models);
    installer
        .begin(Some(choice("openai", "gpt-5.5", Some(KEY))))
        .expect("the install starts");
    let status = over(&installer);
    assert_eq!(
        status.failure.as_ref().map(|failure| failure.step),
        Some("service")
    );
    assert!(status.can_change_models);
    installer
        .begin(Some(choice("ollama", "minimax-m2.7:cloud", None)))
        .expect("a new choice is taken where this session chose the models");
    let status = over(&installer);
    assert_eq!(
        status.failure.as_ref().map(|failure| failure.step),
        Some("service")
    );
    assert_eq!(
        ran(&fixture, "dotagent setup"),
        2,
        "set up again with the new choice"
    );
    // A session that resumed with the models done: the service step fails again, Change the models is not offered, and
    // a choice sent anyway is refused with the failure still shown.
    let resumed = Installer::new(Some(fixture.ctx.clone()));
    resumed
        .begin(None)
        .expect("the install goes on without a choice");
    let status = over(&resumed);
    assert_eq!(
        status.failure.as_ref().map(|failure| failure.step),
        Some("service")
    );
    assert!(!status.can_change_models);
    let refused = resumed
        .begin(Some(choice("openai", "gpt-5.5", Some(KEY))))
        .expect_err("refused");
    assert!(refused.contains("set up already"), "{refused}");
    let status = resumed.status();
    assert_eq!(status.phase, Phase::Failed);
    assert_eq!(
        status.failure.as_ref().map(|failure| failure.step),
        Some("service")
    );
    assert_eq!(
        ran(&fixture, "dotagent setup"),
        2,
        "setup was not run again"
    );
    // The status says it to the page under the name the page reads.
    let json = serde_json::to_value(&status).expect("JSON");
    assert_eq!(json["canChangeModels"], false);
    assert!(json.get("can_change_models").is_none(), "{json}");
}

#[test]
fn a_config_toml_that_is_there_is_the_models_done_whatever_the_marker_recorded() {
    let fixture = Fixture::new("config-there");
    fixture.scratch.write("fail-setup", "");
    let shared = shared_for(choice("openai", "gpt-5.5", Some(KEY)));
    fixture.run(&shared, &|| fixture.nothing_else_is_here());
    assert_eq!(fixture.marker(), recorded(true, false, false));
    // `dotx setup` was run by hand since, in a terminal: the config is there.
    fs::remove_file(fixture.scratch.path("fail-setup")).expect("the flag");
    fixture
        .scratch
        .write("home/config.toml", "# written by the person\n");
    assert_eq!(
        fixture.ctx.found().done(),
        Some(Steps {
            package: true,
            setup: true,
            service: false
        })
    );
    let installer = Installer::new(Some(fixture.ctx.clone()));
    installer.begin(None).expect("the install goes on");
    let status = over(&installer);
    assert_eq!(status.phase, Phase::Done, "{:?}", status.failure);
    assert_eq!(
        ran(&fixture, "dotagent setup"),
        1,
        "only the one that failed"
    );
    assert_eq!(
        fs::read_to_string(fixture.scratch.path("home/config.toml")).expect("the config"),
        "# written by the person\n"
    );
}

/// The marker as the build before this one wrote it: no steps, and whether the daemon had answered.
fn older_marker(fixture: &Fixture, complete: bool) {
    fixture.scratch.write(
        "home/runtime/app-install.json",
        &format!(r#"{{"version":"0.1.0","complete":{complete}}}"#),
    );
}

#[test]
fn a_marker_an_older_build_wrote_is_read_by_what_is_on_the_disk_and_never_traps_an_owner() {
    let mut fixture = Fixture::new("older-marker");
    // The package, the config, the token and the job, as the stand-ins make them.
    complete_install(&fixture, "0.1.0");
    // The daemon is not running at any of the starts below: it is what the disk shows that decides.
    fixture.ctx.answers = |_| false;
    let steps = |package, setup, service| {
        Some(Steps {
            package,
            setup,
            service,
        })
    };
    let plist = fixture.scratch.path(&format!("user/{PLIST}"));
    // Everything is there and the older build never saw the daemon answer (`complete` is false): the install is
    // complete, and the window is the one Dot always had, not "Set up Dot" again.
    older_marker(&fixture, false);
    let found = fixture.ctx.found();
    assert_eq!(found.marker, without_steps());
    assert_eq!(found.done(), steps(true, true, true));
    assert_eq!((decide(&found), resumes(&found)), (Decision::Dot, false));
    // The start's upgrade check writes it again as this build writes it, once: what it says is true from then on.
    assert_eq!(keep_in_step(&fixture.ctx), Ok(None));
    assert_eq!(fixture.marker(), finished());
    let text = fs::read_to_string(fixture.ctx.runtime().marker()).expect("the marker");
    assert!(
        text.contains("\"steps\"") && !text.contains("complete"),
        "{text}"
    );
    // The service is not installed: the models are done, and the installer goes on at the service.
    fs::remove_file(&plist).expect("the job");
    older_marker(&fixture, false);
    let found = fixture.ctx.found();
    assert_eq!(found.done(), steps(true, true, false));
    assert_eq!(
        (decide(&found), resumes(&found)),
        (Decision::Installer, true)
    );
    // No config either: it goes on at the models.
    fs::remove_file(fixture.scratch.path("home/config.toml")).expect("the config");
    let found = fixture.ctx.found();
    assert_eq!(found.done(), steps(true, false, false));
    assert_eq!(
        (decide(&found), resumes(&found)),
        (Decision::Installer, true)
    );
    // An older marker that says the daemon had answered (`"complete": true`) is not believed over the disk either: a
    // step is done only where the disk shows it. With the models' config and the job gone it says nothing more than the
    // package, and the installer goes on at the models.
    older_marker(&fixture, true);
    let found = fixture.ctx.found();
    assert_eq!(found.done(), steps(true, false, false));
    assert_eq!(
        (decide(&found), resumes(&found)),
        (Decision::Installer, true)
    );
    // With no package it says nothing at all.
    fs::remove_dir_all(fixture.ctx.runtime().tool_dir()).expect("the package");
    let found = fixture.ctx.found();
    assert_eq!(found.done(), steps(false, false, false));
    assert_eq!(
        (decide(&found), resumes(&found)),
        (Decision::Installer, true)
    );
}

#[test]
fn a_package_that_is_gone_is_not_done_whatever_the_marker_recorded() {
    let mut fixture = Fixture::new("package-gone");
    complete_install(&fixture, "0.1.0");
    // The daemon is not running: a daemon that answers keeps the installer away whatever else is found.
    fixture.ctx.answers = |_| false;
    assert_eq!(fixture.marker(), finished());
    assert_eq!(decide(&fixture.ctx.found()), Decision::Dot);
    // The person removed the package's environment, and left the rest: the marker still records the package, but the
    // install is not complete, and the installer goes on at the package (the models and the service stay done).
    fs::remove_dir_all(fixture.ctx.runtime().tool_dir()).expect("the package");
    let found = fixture.ctx.found();
    assert_eq!(
        found.done(),
        Some(Steps {
            package: false,
            setup: true,
            service: true
        })
    );
    assert_eq!(
        (decide(&found), resumes(&found)),
        (Decision::Installer, true)
    );
}

#[test]
fn the_page_is_told_whether_the_models_are_done_under_the_name_it_reads() {
    let check = |ok| Check {
        ok,
        detail: String::new(),
    };
    let inspection = Inspection {
        version: "0.1.0".to_string(),
        macos: check(true),
        chrome: check(true),
        ollama: Ollama {
            running: false,
            models: Vec::new(),
        },
        location: check(true),
        providers: Vec::new(),
        resuming: true,
        models_done: true,
        blocked: None,
        log: String::new(),
    };
    let json = serde_json::to_value(&inspection).expect("JSON");
    assert_eq!(json["resuming"], true);
    assert_eq!(json["modelsDone"], true);
    assert!(json.get("models_done").is_none(), "{json}");
}

// ------------------------------------------------------------------- round 5: the job is read exactly, files are not
// waited on, and the upgrade runs where the app stays

use std::os::unix::fs::FileTypeExt;
use std::sync::mpsc;

/// The longest a call that must not wait for a FIFO may take in a test: a call that takes longer is held by it, and the
/// test fails (a non-blocking open ends in microseconds, so that this is long for a Mac under a load of dozens).
const FIFO_WAIT: Duration = Duration::from_secs(15);

/// Run `work` on a thread of its own and wait for it at most `limit`: `None` when it did not end, so that a call that
/// waits for a FIFO fails a test and does not hold it, or the run. The thread is left where it is.
fn within<T: Send + 'static>(
    limit: Duration,
    work: impl FnOnce() -> T + Send + 'static,
) -> Option<T> {
    let (done, waiting) = mpsc::channel();
    thread::spawn(move || {
        let _ = done.send(work());
    });
    waiting.recv_timeout(limit).ok()
}

/// A FIFO at `path`, as a command of a run may make one wherever it may make a file.
fn make_fifo(path: &Path) {
    let made = Command::new("/usr/bin/mkfifo")
        .arg(path)
        .status()
        .expect("mkfifo is a program of the system");
    assert!(made.success(), "mkfifo {}", path.display());
}

/// What the installer's log holds, empty when there is none.
fn log_of(fixture: &Fixture) -> String {
    fs::read_to_string(fixture.ctx.log_dir.join(LOG_FILE)).unwrap_or_default()
}

/// `text` as the text of an XML element: what a path with an `&` in it is written as.
fn xml(text: &str) -> String {
    text.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
}

/// A property list like a job a person or a program could write: the label, the program arguments, and `extra`, what
/// else the dictionary holds.
fn job_with(label: &str, arguments: &[&str], extra: &str) -> String {
    let words: String = arguments
        .iter()
        .map(|word| format!("<string>{}</string>", xml(word)))
        .collect();
    format!(
        r#"<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
<key>Label</key><string>{label}</string>
<key>ProgramArguments</key><array>{words}</array>
<key>RunAtLoad</key><true/>
{extra}
</dict></plist>"#
    )
}

/// The job of the app's own for `home`, as `dotx service install` writes it when the app runs it.
fn job_text(home: &Path) -> String {
    let launcher = launcher_of(home);
    let python = Runtime::of(home).python();
    job_with(
        LABEL,
        &[
            &launcher.display().to_string(),
            &python.display().to_string(),
            "-P",
            "-m",
            "dotagent",
            "daemon",
        ],
        "",
    )
}

/// The verdict `found` gives a property list with this text, for a Dot home at `home` (which need not exist).
fn job_of(test: &str, text: &str, home: &Path) -> Job {
    let scratch = Scratch::new(test);
    scratch.write(&format!("user/{PLIST}"), text);
    found(home, &scratch.path("user"), false).job
}

#[test]
fn which_property_lists_read_as_the_apps_own_job() {
    let home = PathBuf::from("/Users/someone/Library/Application Support/Dot");
    let runtime = Runtime::of(&home);
    let launcher = launcher_of(&home).display().to_string();
    let python = runtime.python().display().to_string();
    let rt = runtime.dir.display().to_string();
    let words = ["-P", "-m", "dotagent", "daemon"];
    let args = |first: &str, second: &str| -> Vec<String> {
        [first, second]
            .into_iter()
            .chain(words)
            .map(str::to_string)
            .collect()
    };
    let job = |label: &str, arguments: Vec<String>, extra: &str| {
        let borrowed: Vec<&str> = arguments.iter().map(String::as_str).collect();
        job_with(label, &borrowed, extra)
    };
    let evil = ["/bin/sh", "-c", "echo planted > /tmp/x"];
    let path_of_a_checkout = format!(
        "<key>EnvironmentVariables</key><dict><key>PATH</key><string>{rt}/app-tools:/usr/bin</string></dict>"
    );
    let table: Vec<(&str, String, Job)> = vec![
        (
            "what dotx service install writes, by the renderer of Dot (app-job.plist)",
            include_str!("app-job.plist").to_string(),
            Job::Ours,
        ),
        ("the same, in this test's words", job_text(&home), Job::Ours),
        (
            "the same with doubled slashes: a path is a path",
            job(LABEL, args(&launcher.replace("/Users", "//Users"), &python), ""),
            Job::Ours,
        ),
        (
            "a checkout's job on this home: its PATH names runtime/app-tools, its Python is the checkout's",
            job(
                LABEL,
                args(&launcher, "/Users/someone/Dot/.venv/bin/python"),
                &path_of_a_checkout,
            ),
            Job::Other,
        ),
        (
            "/bin/sh, no mention of the runtime folder",
            job_with(LABEL, &evil, ""),
            Job::Other,
        ),
        (
            "/bin/sh, the runtime folder in an XML comment",
            job_with(LABEL, &evil, &format!("<!-- {rt} -->")),
            Job::Other,
        ),
        (
            "/bin/sh, the runtime folder in an unrelated key",
            job_with(LABEL, &evil, &format!("<key>Note</key><string>{rt}</string>")),
            Job::Other,
        ),
        (
            "/bin/sh, a PATH that names runtime/app-tools",
            job_with(LABEL, &evil, &path_of_a_checkout),
            Job::Other,
        ),
        (
            "a program of runtime-evil, beside the runtime folder",
            job(LABEL, args(&launcher, &format!("{rt}-evil/bin/python")), ""),
            Job::Other,
        ),
        (
            "launchd runs /bin/sh, and the runtime's Python is only its argument",
            job(LABEL, args("/bin/sh", &python), ""),
            Job::Other,
        ),
        (
            "the right arguments under another label",
            job("com.evil.other", args(&launcher, &python), ""),
            Job::Other,
        ),
        (
            "a Program of its own, which launchd runs and not the first word",
            job(
                LABEL,
                args(&launcher, &python),
                "<key>Program</key><string>/bin/sh</string>",
            ),
            Job::Other,
        ),
        (
            "the Python reached through a .. component",
            job(
                LABEL,
                args(&launcher, &format!("{rt}/uv-tools/dotagent-macos/bin/../bin/python")),
                "",
            ),
            Job::Other,
        ),
        (
            "the launcher reached through a .. component",
            job(
                LABEL,
                args(
                    &launcher.replace("/MacOS/Dot", "/MacOS/../MacOS/Dot"),
                    &python,
                ),
                "",
            ),
            Job::Other,
        ),
        (
            "the launcher alone",
            job(LABEL, vec![launcher.clone()], ""),
            Job::Other,
        ),
        (
            "a word that is not a string between the two right ones",
            format!(
                r#"<plist version="1.0"><dict><key>Label</key><string>{LABEL}</string><key>ProgramArguments</key><array><string>{launcher}</string><integer>1</integer><string>{python}</string></array></dict></plist>"#
            ),
            Job::Other,
        ),
        (
            "the label and the arguments in a nested dictionary only",
            format!(
                r#"<plist version="1.0"><dict><key>Inner</key><dict><key>Label</key><string>{LABEL}</string><key>ProgramArguments</key><array><string>{launcher}</string><string>{python}</string></array></dict></dict></plist>"#
            ),
            Job::Other,
        ),
        (
            "a property list that is not a dictionary",
            r#"<plist version="1.0"><array/></plist>"#.to_string(),
            Job::Other,
        ),
        (
            "text that is no property list, and a file with nothing in it",
            "<string>/Users/someone/Library/Application Support/Dot/runtime/x</string>".to_string(),
            Job::Other,
        ),
        ("an empty file", String::new(), Job::Other),
    ];
    for (number, (what, text, expected)) in table.iter().enumerate() {
        assert_eq!(
            job_of(&format!("job-table-{number}"), text, &home),
            *expected,
            "{what}"
        );
    }
}

#[test]
fn the_property_list_dotx_service_install_writes_is_the_job_of_its_own_home_and_no_other() {
    // `app-job.plist` is what the renderer of `launchd.py` writes for this home: `tests/unit/test_installer_files.py`
    // keeps the file equal to it, so that what this reads is what Python writes.
    let home = PathBuf::from("/Users/someone/Library/Application Support/Dot");
    let text = include_str!("app-job.plist");
    assert_eq!(job_of("job-real-own", text, &home), Job::Ours);
    // The job of another home names another Dot.app and another Python.
    let other = PathBuf::from("/Users/someone/Library/Application Support/Dot2");
    assert_eq!(job_of("job-real-other-home", text, &other), Job::Other);
    // A home with an ampersand in its path: XML writes it `&amp;`, and it reads back as the path.
    let ampersand = PathBuf::from("/Users/a&b/Library/Application Support/Dot");
    let escaped = text.replace("/Users/someone", "/Users/a&amp;b");
    assert!(escaped.contains("a&amp;b"));
    assert_eq!(job_of("job-real-amp", &escaped, &ampersand), Job::Ours);
    assert_eq!(
        job_of("job-amp", &job_text(&ampersand), &ampersand),
        Job::Ours
    );
}

#[test]
fn a_property_list_that_is_a_link_a_fifo_or_too_large_is_another_programs_and_is_never_waited_on() {
    let home = PathBuf::from("/Users/someone/Library/Application Support/Dot");
    let scratch = Scratch::new("odd-plists");
    let user = scratch.path("user");
    let plist = user.join(format!("Library/LaunchAgents/{LAUNCH_AGENT}"));
    fs::create_dir_all(plist.parent().expect("a folder")).expect("the folder of the agents");
    let job_of_the_plist = {
        let (home, user) = (home.clone(), user.clone());
        move || found(&home, &user, false).job
    };
    // A link, even to the app's own job: what is at the name is not what `dotx service install` wrote.
    let real = scratch.write("real.plist", &job_text(&home));
    symlink(&real, &plist).expect("a link");
    assert_eq!(job_of_the_plist(), Job::Other);
    fs::remove_file(&plist).expect("the link");
    // A FIFO: read as another program's, at once.
    make_fifo(&plist);
    let verdict = within(FIFO_WAIT, job_of_the_plist.clone())
        .expect("reading the property list waited for a FIFO");
    assert_eq!(verdict, Job::Other);
    fs::remove_file(&plist).expect("the FIFO");
    // Longer than the shell reads: a valid job followed by more white space than that. Cut at the limit it would still
    // be the job, so that it is the length, and not a broken document, that is refused.
    let padding = " ".repeat(usize::try_from(READ_LIMIT).expect("a size"));
    fs::write(&plist, format!("{}{padding}", job_text(&home))).expect("a long property list");
    assert_eq!(job_of_the_plist(), Job::Other);
    fs::write(&plist, job_text(&home)).expect("the job again");
    assert_eq!(
        job_of_the_plist(),
        Job::Ours,
        "the control: the same job, not padded"
    );
    // Nested as deep as the limit allows: read as another program's, and the shell is still there.
    let depth = 4_000;
    let deep = format!("{}{}", "<array>".repeat(depth), "</array>".repeat(depth));
    let nested = format!(
        r#"<plist version="1.0"><dict><key>Label</key><string>{LABEL}</string><key>X</key>{deep}</dict></plist>"#
    );
    assert!(u64::try_from(nested.len()).expect("a size") < READ_LIMIT);
    fs::write(&plist, nested).expect("a deep property list");
    let verdict = within(FIFO_WAIT, job_of_the_plist).expect("the deep list was read");
    assert_eq!(verdict, Job::Other);
}

/// An install that stopped after the models (the service step failed before it made anything), the app opened again
/// and no daemon answering at any start of the test: what the disk shows decides.
fn stopped_after_the_models(test: &str) -> Fixture {
    let mut fixture = Fixture::new(test);
    fixture.scratch.write("fail-service-early", "");
    let shared = shared_for(choice("openai", "gpt-5.5", Some(KEY)));
    fixture.run(&shared, &|| fixture.nothing_else_is_here());
    fs::remove_file(fixture.scratch.path("fail-service-early")).expect("the flag");
    assert_eq!(fixture.marker(), recorded(true, true, false));
    fixture.ctx.answers = |_| false;
    fixture
}

#[test]
fn a_planted_job_that_mentions_the_runtime_folder_does_not_make_an_unfinished_install_finished() {
    let fixture = stopped_after_the_models("planted-job");
    let rt = fixture.ctx.runtime().dir.display().to_string();
    let plist = fixture.scratch.path(&format!("user/{PLIST}"));
    // Another program's job, with a comment that names the runtime folder (what the substring match took for the app's).
    fixture.scratch.write(
        &format!("user/{PLIST}"),
        &job_with(
            LABEL,
            &["/bin/sh", "-c", "echo planted > /tmp/x"],
            &format!("<!-- {rt} -->"),
        ),
    );
    let found = fixture.ctx.found();
    assert_eq!(found.job, Job::Other);
    assert_eq!(
        found.done(),
        Some(Steps {
            package: true,
            setup: true,
            service: false
        })
    );
    // The installer does not run over a job that is not its own, and nothing is upgraded or restarted for it.
    assert_eq!(foreign(&found), Some(Reason::ServiceInstalled));
    assert_eq!(decide(&found), Decision::Dot);
    let calls = fixture.calls();
    let mut ctx = fixture.ctx.clone();
    ctx.version = "0.2.0".to_string();
    assert_eq!(keep_in_step(&ctx), Ok(None));
    assert_eq!(fixture.calls(), calls);
    // The marker was not made to say the service is installed: when the planted job goes, the installer is offered.
    assert_eq!(fixture.marker(), recorded(true, true, false));
    fs::remove_file(&plist).expect("the planted job goes");
    let found = fixture.ctx.found();
    assert_eq!(
        (decide(&found), resumes(&found)),
        (Decision::Installer, true)
    );
}

#[test]
fn the_upgrade_leaves_the_job_of_another_dot_alone() {
    let mut fixture = Fixture::new("foreign-job");
    complete_install(&fixture, "0.1.0");
    fixture.ctx.answers = |_| false;
    // A checkout's `dotx service install` took the job's place: the same label and launcher, the checkout's Python.
    let launcher = launcher_of(&fixture.ctx.home).display().to_string();
    fixture.scratch.write(
        &format!("user/{PLIST}"),
        &job_with(
            LABEL,
            &[
                &launcher,
                "/Users/someone/Dot/.venv/bin/python",
                "-P",
                "-m",
                "dotagent",
                "daemon",
            ],
            "",
        ),
    );
    let found = fixture.ctx.found();
    assert_eq!(found.job, Job::Other);
    assert_eq!(foreign(&found), Some(Reason::ServiceInstalled));
    assert_eq!(decide(&found), Decision::Dot);
    let mut ctx = fixture.ctx.clone();
    ctx.version = "0.2.0".to_string();
    assert_eq!(keep_in_step(&ctx), Ok(None));
    // No upgrade, and no restart of a daemon that is not the app's to restart.
    assert_eq!(fixture.calls(), Vec::<String>::new());
    assert_eq!(installed_version(&ctx.runtime()).as_deref(), Some("0.1.0"));
    assert!(
        log_of(&fixture).contains("not the one this app installed"),
        "{}",
        log_of(&fixture)
    );
}

#[test]
fn an_app_that_runs_from_a_disk_image_or_a_translocated_copy_leaves_a_complete_install_alone() {
    for (test, app) in [
        ("on-an-image", "/Volumes/Dot/Dot.app"),
        (
            "translocated",
            "/private/var/folders/xy/T/AppTranslocation/0C1E/d/Dot.app",
        ),
    ] {
        let fixture = Fixture::new(test);
        complete_install(&fixture, "0.1.0");
        let links = links_of(&fixture.ctx.runtime().tools_folder());
        // A newer app, opened from a place it will not stay in: its tools are there, and so would the links be.
        let mut ctx = fixture.ctx.clone();
        ctx.version = "0.2.0".to_string();
        ctx.app = Some(PathBuf::from(app));
        ctx.tools = Tools {
            dir: fixture.scratch.path("mounted/tools"),
        };
        assert_eq!(keep_in_step(&ctx), Ok(None), "{app}");
        assert_eq!(
            fixture.calls(),
            Vec::<String>::new(),
            "no upgrade, no restart: {app}"
        );
        assert_eq!(installed_version(&ctx.runtime()).as_deref(), Some("0.1.0"));
        assert_eq!(
            links_of(&ctx.runtime().tools_folder()),
            links,
            "the links were not moved into a place that goes away: {app}"
        );
        assert!(
            log_of(&fixture).contains("not a place it stays in"),
            "{}",
            log_of(&fixture)
        );
        // The same app from where it stays: upgraded, and the links follow it. (A run from no app at all, as a
        // development build is, was never refused.)
        ctx.app = Some(PathBuf::from("/Applications/Dot.app"));
        ctx.tools = fixture.ctx.tools.clone();
        assert!(keep_in_step(&ctx).expect("the upgrade works").is_some());
        assert_eq!(installed_version(&ctx.runtime()).as_deref(), Some("0.2.0"));
    }
}

#[test]
fn a_marker_that_cannot_be_written_is_said_in_the_log_and_the_start_goes_on() {
    let mut fixture = Fixture::new("marker-unwritable");
    complete_install(&fixture, "0.1.0");
    fixture.ctx.answers = |_| false;
    // The marker of an older build is written again as this build writes it: make that write fail, with a folder that
    // cannot be emptied at the name the marker is staged at.
    older_marker(&fixture, false);
    let staging = fixture.ctx.runtime().dir.join(format!(".{MARKER}.tmp"));
    fs::create_dir(&staging).expect("a folder at the staging name");
    fs::write(staging.join("held"), "x").expect("a file in it");
    fs::set_permissions(&staging, fs::Permissions::from_mode(0o500))
        .expect("a folder that is held");
    let said = keep_in_step(&fixture.ctx);
    fs::set_permissions(&staging, fs::Permissions::from_mode(0o700)).expect("the folder is let go");
    // The start went on: nothing to upgrade, no failure shown, and the failure is in the log.
    assert_eq!(said, Ok(None));
    let log = log_of(&fixture);
    assert!(
        log.contains("The install's marker could not be written"),
        "{log}"
    );
    // The marker is as it was, and the disk says what is done at every start until it can be written.
    assert_eq!(fixture.marker(), without_steps());
    assert_eq!(decide(&fixture.ctx.found()), Decision::Dot);
}

#[test]
fn a_fifo_at_the_logs_name_does_not_hold_the_upgrade() {
    let fixture = Fixture::new("fifo-log");
    complete_install(&fixture, "0.1.0");
    // A command of a run may make a FIFO in Dot's log folder. Opened for appending, it would hold the app at the log
    // until a program that never comes reads it, and the owner would stay on the old package after every update.
    let log = fixture.ctx.log_dir.join(LOG_FILE);
    fs::remove_file(&log).expect("the log of the install");
    make_fifo(&log);
    let mut ctx = fixture.ctx.clone();
    ctx.version = "0.2.0".to_string();
    let said = within(FIFO_WAIT, move || keep_in_step(&ctx))
        .expect("the upgrade waited for a FIFO at the log's name");
    assert!(said.expect("the upgrade works").is_some());
    assert_eq!(ran(&fixture, "tool install --reinstall"), 1);
    assert!(fs::symlink_metadata(&log)
        .expect("the FIFO is still there")
        .file_type()
        .is_fifo());
    // A line written to a log that could not be opened is dropped, and nothing waits.
    let dir = fixture.ctx.log_dir.clone();
    within(FIFO_WAIT, move || {
        Log::open(&dir).line("a line for a log that is not there");
    })
    .expect("opening the log waited for a FIFO");
}

#[test]
fn a_fifo_where_the_marker_or_the_job_belongs_is_not_waited_on_and_a_long_marker_is_not_read() {
    let mut fixture = Fixture::new("fifo-marker");
    complete_install(&fixture, "0.1.0");
    fixture.ctx.answers = |_| false;
    let marker = fixture.ctx.runtime().marker();
    fs::remove_file(&marker).expect("the marker");
    make_fifo(&marker);
    let ctx = fixture.ctx.clone();
    let found =
        within(FIFO_WAIT, move || ctx.found()).expect("reading the marker waited for a FIFO");
    assert_eq!(found.marker, None);
    fs::remove_file(&marker).expect("the FIFO");
    // A marker longer than the shell reads is no marker, though what is cut off is only white space.
    let padding = " ".repeat(usize::try_from(READ_LIMIT).expect("a size"));
    fixture.scratch.write(
        "home/runtime/app-install.json",
        &format!(r#"{{"version":"0.1.0"}}{padding}"#),
    );
    assert_eq!(fixture.ctx.found().marker, None);
    fixture
        .scratch
        .write("home/runtime/app-install.json", r#"{"version":"0.1.0"}"#);
    assert_eq!(
        fixture.ctx.found().marker,
        without_steps(),
        "the control: the same marker, not padded"
    );
    let plist = fixture.scratch.path(&format!("user/{PLIST}"));
    fs::remove_file(&plist).expect("the job");
    make_fifo(&plist);
    let ctx = fixture.ctx.clone();
    let found = within(FIFO_WAIT, move || ctx.found())
        .expect("reading the property list waited for a FIFO");
    assert_eq!(found.job, Job::Other);
}

#[test]
fn only_a_regular_file_is_opened_and_it_is_opened_blocking() {
    let scratch = Scratch::new("open-regular");
    let plain = scratch.write("plain.txt", "text");
    let file = open_regular(&plain, OpenOptions::new().read(true)).expect("a plain file opens");
    // SAFETY: the descriptor is that of `file`, open for the whole of this test; `F_GETFL` only reads its flags.
    let flags = unsafe { libc::fcntl(file.as_raw_fd(), libc::F_GETFL) };
    assert_eq!(
        flags & libc::O_NONBLOCK,
        0,
        "the descriptor goes back to blocking"
    );
    let link = scratch.path("link");
    symlink(&plain, &link).expect("a link");
    let error =
        open_regular(&link, OpenOptions::new().read(true)).expect_err("a link is not followed");
    assert_eq!(error.raw_os_error(), Some(libc::ELOOP));
    let error = open_regular(&scratch.path(""), OpenOptions::new().read(true))
        .expect_err("a folder is not a file");
    assert!(error.to_string().contains("not a regular file"), "{error}");
    let fifo = scratch.path("fifo");
    make_fifo(&fifo);
    // Every open of the FIFO has a time to end in: without `O_NONBLOCK` an open for reading waits for a writer, and an
    // open for writing for a reader, which never come, and a test that called them straight would hold the run.
    let for_reading = {
        let fifo = fifo.clone();
        within(FIFO_WAIT, move || {
            open_regular(&fifo, OpenOptions::new().read(true)).map(drop)
        })
    }
    .expect("the open of a FIFO for reading waited for a writer");
    let error = for_reading.expect_err("a FIFO is not a file");
    assert!(error.to_string().contains("not a regular file"), "{error}");
    // For writing, with nobody to read, it is an error at once and not a wait.
    let for_writing = within(FIFO_WAIT, move || {
        open_regular(&fifo, OpenOptions::new().write(true)).map(drop)
    })
    .expect("the open of a FIFO for writing waited for a reader");
    assert!(for_writing.is_err());
}

/// The package, as a stand-in: the Python the shell looks for and the metadata folder that names its version.
fn stand_in_package(fixture: &Fixture, version: &str) {
    let environment = fixture.ctx.runtime().tool_dir().join(PACKAGE);
    fs::create_dir_all(environment.join("bin")).expect("bin");
    fs::create_dir_all(environment.join(format!(
        "lib/python3.13/site-packages/dotagent_macos-{version}.dist-info"
    )))
    .expect("the metadata folder");
    fixture.scratch.script(
        "home/runtime/uv-tools/dotagent-macos/bin/python",
        FAKE_PYTHON,
    );
}

#[test]
fn an_older_marker_that_says_complete_over_a_package_alone_resumes_and_triggers_no_upgrade() {
    let mut fixture = Fixture::new("older-complete");
    fixture.ctx.answers = |_| false;
    // Only the package's Python is there: no config, no job, no token, no links to the tools. A marker that claims the
    // daemon answered (anyone who may write the runtime folder may write that) is not believed over the disk.
    stand_in_package(&fixture, "0.1.0");
    fixture.scratch.write(
        "home/runtime/app-install.json",
        r#"{"version":"0.0.0","complete":true}"#,
    );
    let found = fixture.ctx.found();
    assert_eq!(
        found.done(),
        Some(Steps {
            package: true,
            setup: false,
            service: false
        })
    );
    assert_eq!(
        (decide(&found), resumes(&found)),
        (Decision::Installer, true)
    );
    let mut ctx = fixture.ctx.clone();
    ctx.version = "0.2.0".to_string();
    assert_eq!(keep_in_step(&ctx), Ok(None));
    assert_eq!(
        fixture.calls(),
        Vec::<String>::new(),
        "nothing is upgraded or restarted"
    );
    assert!(
        !ctx.runtime().tools_folder().exists(),
        "no link to the app's tools was made"
    );
    assert_eq!(installed_version(&ctx.runtime()).as_deref(), Some("0.1.0"));
}

#[test]
fn the_log_names_the_date_as_a_calendar_does() {
    assert_eq!(civil(0), "1970-01-01T00:00:00Z");
    assert_eq!(civil(1_700_000_000), "2023-11-14T22:13:20Z");
    assert_eq!(civil(1_709_164_800), "2024-02-29T00:00:00Z");
    assert_eq!(civil(1_735_689_599), "2024-12-31T23:59:59Z");
}

#[test]
fn macos_is_new_enough_from_15_2() {
    for (version, ok) in [
        ("15.2", true),
        ("15.2.1", true),
        ("26.0.1", true),
        ("15.1.9", false),
        ("15", false),
        ("14.7", false),
        ("garbage", false),
    ] {
        assert_eq!(at_least(version, MIN_MACOS), ok, "{version}");
    }
}

#[test]
fn the_page_may_open_chrome_and_ollama_pages_and_the_log_and_nothing_else() {
    let log = Path::new("/Users/me/Library/Logs/Dot/installer.log");
    assert_eq!(
        open_args(Open::Chrome, log),
        [OsString::from("https://www.google.com/chrome/")]
    );
    assert_eq!(
        open_args(Open::Ollama, log),
        [OsString::from("https://ollama.com/download")]
    );
    assert_eq!(
        open_args(Open::Log, log),
        [OsString::from("-R"), log.into()]
    );
}

#[test]
fn the_window_shows_the_installers_own_page_and_no_other_address() {
    let page = |address: &str| url::Url::parse(address).expect("an address");
    assert!(is_installer_page(&page("tauri://localhost/installer.html")));
    assert!(is_installer_page(&page(
        "tauri://localhost/installer.html#again"
    )));
    for other in [
        "tauri://localhost/down.html",
        "tauri://localhost/installer.html/../down.html",
        "tauri://localhost/",
        "http://127.0.0.1:8765/",
        "https://tauri.localhost/installer.html",
        "tauri://example.com/installer.html",
        "file:///Applications/Dot.app/Contents/Resources/installer.html",
    ] {
        assert!(!is_installer_page(&page(other)), "{other}");
    }
}

// What follows is the claim the whole design rests on, tested on the real thing: the app built with its own
// capabilities (`tauri::generate_context!` reads `capabilities/` and the manifest `build.rs` made), asked for each
// command from the pages that must be refused and the one that must not.

fn kebab(command: &str) -> String {
    command.replace('_', "-")
}

fn permissions_of(file: &str) -> Vec<String> {
    let json: serde_json::Value = serde_json::from_str(file).expect("a capability file is JSON");
    json["permissions"]
        .as_array()
        .expect("a list of permissions")
        .iter()
        .map(|entry| entry.as_str().expect("a permission by name").to_string())
        .collect()
}

#[test]
fn the_commands_are_the_same_in_the_build_script_the_handler_and_the_capability() {
    let build = include_str!("../../build.rs");
    let lib = include_str!("../lib.rs");
    let capability = include_str!("../../capabilities/installer.json");
    let named: Vec<&str> = build
        .split('"')
        .filter(|word| word.starts_with("installer_"))
        .collect();
    assert_eq!(named, COMMANDS);
    for command in COMMANDS {
        assert!(
            lib.contains(&format!("installer::{command},")),
            "{command} is not in the handler"
        );
        assert!(
            permissions_of(capability).contains(&format!("allow-{}", kebab(command))),
            "{command} is not granted to the installer"
        );
    }
}

#[test]
fn only_the_installers_capability_names_the_commands_and_it_names_the_installers_window_and_local_pages(
) {
    let json: serde_json::Value =
        serde_json::from_str(include_str!("../../capabilities/installer.json")).expect("JSON");
    assert_eq!(json["windows"], serde_json::json!([WINDOW]));
    assert_eq!(json["local"], serde_json::json!(true));
    assert!(
        json.get("remote").is_none(),
        "a remote entry would give the commands to pages of an address"
    );
    assert!(json.get("webviews").is_none());
    let granted = permissions_of(include_str!("../../capabilities/installer.json"));
    let others: Vec<&String> = granted
        .iter()
        .filter(|name| !name.starts_with("allow-installer-"))
        .collect();
    assert_eq!(
        others,
        [
            "core:window:allow-start-dragging",
            "core:window:allow-internal-toggle-maximize"
        ]
    );
    // The window `main`'s capability and the grant to the daemon's pages name none of them.
    for permission in permissions_of(include_str!("../../capabilities/default.json"))
        .iter()
        .map(String::as_str)
        .chain(crate::PAGE_PERMISSIONS)
    {
        assert!(!permission.contains("installer"), "{permission}");
    }
    let main: serde_json::Value =
        serde_json::from_str(include_str!("../../capabilities/default.json")).expect("JSON");
    assert_eq!(main["windows"], serde_json::json!([crate::WINDOW]));
}

fn app() -> tauri::App<MockRuntime> {
    crate::build_app(tauri::test::mock_builder().manage(Installer::new(None)))
}

fn ask(
    webview: &WebviewWindow<MockRuntime>,
    command: &str,
    from: &str,
) -> Result<InvokeResponseBody, serde_json::Value> {
    get_ipc_response(
        webview,
        InvokeRequest {
            cmd: command.into(),
            callback: CallbackFn(0),
            error: CallbackFn(1),
            url: from.parse().expect("an address"),
            body: InvokeBody::default(),
            headers: Default::default(),
            invoke_key: INVOKE_KEY.to_string(),
        },
    )
}

fn refused_by_the_acl(answer: &Result<InvokeResponseBody, serde_json::Value>) -> bool {
    matches!(answer, Err(error) if error.as_str().is_some_and(|words| words.contains("not allowed")))
}

#[test]
fn no_page_but_the_installers_own_can_call_the_install_commands() {
    let app = app();
    let installer = WebviewWindowBuilder::new(&app, WINDOW, WebviewUrl::App(PAGE.into()))
        .build()
        .expect("the installer's window");
    let main = WebviewWindowBuilder::new(&app, crate::WINDOW, WebviewUrl::App("down.html".into()))
        .build()
        .expect("the main window");
    // What `lib.rs` grants the pages of the daemon's address when the daemon proves itself.
    let daemon = crate::Daemon {
        base: "http://127.0.0.1:8765".to_string(),
        token: "unused".to_string(),
    };
    app.add_capability(crate::daemon_capability(&daemon))
        .expect("the daemon's grant");
    let own_page = "tauri://localhost/installer.html";
    // The control: the installer's own page is answered, so that a refusal below is the ACL's and not a mistake of
    // the test.
    let status = ask(&installer, "installer_status", own_page);
    assert!(status.is_ok(), "{status:?}");
    for command in COMMANDS {
        let answer = ask(&installer, command, own_page);
        assert!(
            !refused_by_the_acl(&answer),
            "{command} from its own page: {answer:?}"
        );
        for (window, from, who) in [
            (
                &installer,
                "http://127.0.0.1:8765/",
                "a page of the daemon's address inside the installer window",
            ),
            (
                &installer,
                "http://127.0.0.1:8766/",
                "a page of another port",
            ),
            (&installer, "https://example.com/", "a page of the web"),
            (
                &main,
                "http://127.0.0.1:8765/#token=x",
                "the daemon's UI in the main window",
            ),
            (
                &main,
                "http://127.0.0.1:8766/",
                "a page of another port in the main window",
            ),
            (
                &main,
                "tauri://localhost/down.html",
                "the shell's own down page in the main window",
            ),
            (
                &main,
                own_page,
                "the installer's page loaded into the main window",
            ),
        ] {
            let answer = ask(window, command, from);
            assert!(
                refused_by_the_acl(&answer),
                "{command} from {who} was not refused: {answer:?}"
            );
        }
    }
}
