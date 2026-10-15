/*
 * The program of Dot.app (item 288), which launchd runs for the Dot service. It starts the program its arguments name,
 * the daemon's Python, as its child and waits for it, so that macOS holds Dot.app responsible for the daemon: its
 * privacy questions and System Settings name Dot, and what the owner grants Dot holds when Python changes. The signals
 * launchd sends are handed on to the child, and the launcher ends as the child ended.
 */
#include <errno.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;

static const int HANDED_ON[] = {SIGTERM, SIGINT, SIGHUP, SIGQUIT, SIGUSR1, SIGUSR2};
static volatile pid_t child = 0;

static void hand_on(int signal_number) {
  if (child > 0) {
    kill(child, signal_number);
  }
}

int main(int argc, char *argv[]) {
  if (argc < 2) {
    fprintf(stderr, "Dot: no program to start\n");
    return 64;
  }
  sigset_t handed;
  sigset_t before;
  sigemptyset(&handed);
  struct sigaction action;
  memset(&action, 0, sizeof action);
  action.sa_handler = hand_on;
  sigemptyset(&action.sa_mask);
  for (size_t index = 0; index < sizeof HANDED_ON / sizeof HANDED_ON[0]; index++) {
    sigaddset(&handed, HANDED_ON[index]);
    sigaction(HANDED_ON[index], &action, NULL);
  }
  /* A signal that comes while the child starts waits until it can be handed on; the child starts with the signal mask
   * and the actions this program had. */
  sigprocmask(SIG_BLOCK, &handed, &before);
  posix_spawnattr_t attributes;
  posix_spawnattr_init(&attributes);
  posix_spawnattr_setsigmask(&attributes, &before);
  posix_spawnattr_setsigdefault(&attributes, &handed);
  posix_spawnattr_setflags(&attributes, POSIX_SPAWN_SETSIGMASK | POSIX_SPAWN_SETSIGDEF);
  pid_t started = 0;
  int failed = posix_spawn(&started, argv[1], NULL, &attributes, &argv[1], environ);
  posix_spawnattr_destroy(&attributes);
  if (failed != 0) {
    fprintf(stderr, "Dot: cannot start %s: %s\n", argv[1], strerror(failed));
    return 71;
  }
  child = started;
  sigprocmask(SIG_SETMASK, &before, NULL);
  int status = 0;
  while (waitpid(started, &status, 0) < 0) {
    if (errno != EINTR) {
      fprintf(stderr, "Dot: cannot wait for %s: %s\n", argv[1], strerror(errno));
      return 71;
    }
  }
  if (WIFSIGNALED(status)) {
    signal(WTERMSIG(status), SIG_DFL);
    sigprocmask(SIG_SETMASK, &before, NULL);
    raise(WTERMSIG(status));
  }
  return WIFEXITED(status) ? WEXITSTATUS(status) : 70;
}
