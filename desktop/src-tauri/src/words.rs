//! The shell's own words (its menu, the menu bar item's tip, what it tells the down page, the update dialogs) in the
//! language the web UI in its window speaks: English or Turkish, as `[desktop_app] language` of the daemon's config.toml
//! says (the page writes it there, `shelllanguage.js`). A template's `{name}` places are filled by `fill`.

/// The language of the shell's words.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Lang {
    En,
    Tr,
}

impl Lang {
    /// The language `code` names in the config; one the shell does not speak is English, as the web UI's default.
    pub fn of(code: &str) -> Lang {
        match code {
            "tr" => Lang::Tr,
            _ => Lang::En,
        }
    }

    /// The words of this language.
    pub fn words(self) -> &'static Words {
        match self {
            Lang::En => &EN,
            Lang::Tr => &TR,
        }
    }
}

/// Every phrase the shell says, one field each.
pub struct Words {
    pub open: &'static str,
    pub new_chat: &'static str,
    pub shortcut_taken: &'static str,
    pub check_updates: &'static str,
    pub quit: &'static str,
    pub tip_setting_up: &'static str,
    /// `{waiting}`, `{working}`.
    pub tip_counted: &'static str,
    pub tip_silent: &'static str,
    pub tip_unproven: &'static str,
    pub tip_config: &'static str,
    pub tip_token: &'static str,
    pub tip_no_token: &'static str,
    /// `{progress}`: a percentage or the megabytes so far.
    pub tip_downloading: &'static str,
    pub tip_installing: &'static str,
    pub title_config: &'static str,
    pub title_token: &'static str,
    /// `{address}`.
    pub title_unproven: &'static str,
    /// `{listening}`: the command that shows what listens.
    pub detail_unproven: &'static str,
    pub detail_no_token: &'static str,
    pub detail_empty_token: &'static str,
    pub looking_already: &'static str,
    /// `{progress}`.
    pub downloading_already: &'static str,
    /// `{current}`.
    pub newest: &'static str,
    /// `{error}`.
    pub could_not_look: &'static str,
    pub newer_title: &'static str,
    /// `{version}`, `{current}`.
    pub newer_body: &'static str,
    pub install: &'static str,
    pub later: &'static str,
    /// `{error}`.
    pub could_not_install: &'static str,
}

const EN: Words = Words {
    open: "Open Dot",
    new_chat: "New chat",
    shortcut_taken: "⇧⌘Space is taken by another app",
    check_updates: "Check for Updates…",
    quit: "Quit Dot",
    tip_setting_up: "Dot · being set up",
    tip_counted: "Dot · {waiting} waiting for you · {working} working",
    tip_silent: "Dot · the daemon is not running",
    tip_unproven: "Dot · something else answers at the daemon's address",
    tip_config: "Dot · its config.toml could not be read",
    tip_token: "Dot · its token could not be read",
    tip_no_token: "Dot · the daemon has not made its token yet",
    tip_downloading: "Dot · downloading the update · {progress}",
    tip_installing: "Dot · installing the update",
    title_config: "Dot could not read its config.toml",
    title_token: "Dot could not read its token",
    title_unproven: "Something else answers at {address}",
    detail_unproven: "It does not prove that it holds this Dot's token, so the shell sent it nothing: another program \
        holds the port, or a daemon of another Dot home or of an older version answers. See what listens with \
        '{listening}', stop it, and this window opens the web UI by itself.",
    detail_no_token: "there is none yet; the daemon makes it the first time it starts",
    detail_empty_token: "it is empty; delete it and the daemon makes a new one when it starts",
    looking_already: "Dot is looking for an update already.",
    downloading_already: "Dot is downloading the update already ({progress}).",
    newest: "Dot {current} is the newest.",
    could_not_look: "Dot could not look for an update: {error}",
    newer_title: "A newer Dot",
    newer_body: "Dot {version} is out (this one is {current}). Install it now? Dot starts again with it.",
    install: "Install and Restart",
    later: "Later",
    could_not_install: "The update could not be installed: {error}",
};

const TR: Words = Words {
    open: "Dot'u Aç",
    new_chat: "Yeni sohbet",
    shortcut_taken: "⇧⌘Space başka bir uygulamada",
    check_updates: "Güncellemeleri Denetle…",
    quit: "Dot'tan Çık",
    tip_setting_up: "Dot · kuruluyor",
    tip_counted: "Dot · {waiting} sizi bekliyor · {working} çalışıyor",
    tip_silent: "Dot · arka plan hizmeti çalışmıyor",
    tip_unproven: "Dot · hizmetin adresinde başka bir şey yanıt veriyor",
    tip_config: "Dot · config.toml dosyası okunamadı",
    tip_token: "Dot · anahtarı (token) okunamadı",
    tip_no_token: "Dot · arka plan hizmeti anahtarını henüz oluşturmadı",
    tip_downloading: "Dot · güncelleme indiriliyor · {progress}",
    tip_installing: "Dot · güncelleme yükleniyor",
    title_config: "Dot, config.toml dosyasını okuyamadı",
    title_token: "Dot, anahtarını (token) okuyamadı",
    title_unproven: "{address} adresinde başka bir şey yanıt veriyor",
    detail_unproven: "Bu Dot'un anahtarını taşıdığını kanıtlamıyor, bu yüzden uygulama ona hiçbir şey göndermedi: \
        bağlantı noktasını başka bir program tutuyor ya da başka bir Dot klasörünün veya eski bir sürümün arka plan \
        hizmeti yanıt veriyor. Neyin dinlediğini '{listening}' ile görün, onu durdurun; bu pencere web arayüzünü \
        kendiliğinden açar.",
    detail_no_token: "henüz yok; arka plan hizmeti ilk başladığında oluşturur",
    detail_empty_token: "boş; silin, arka plan hizmeti başladığında yenisini oluşturur",
    looking_already: "Dot zaten bir güncelleme arıyor.",
    downloading_already: "Dot güncellemeyi zaten indiriyor ({progress}).",
    newest: "Dot {current} en yeni sürüm.",
    could_not_look: "Dot güncelleme arayamadı: {error}",
    newer_title: "Dot'un yeni sürümü",
    newer_body: "Dot {version} çıktı (bu sürüm {current}). Şimdi yüklensin mi? Dot onunla yeniden başlar.",
    install: "Yükle ve Yeniden Başlat",
    later: "Sonra",
    could_not_install: "Güncelleme yüklenemedi: {error}",
};

/// `template` with each `{name}` of `values` put in its place.
pub fn fill(template: &str, values: &[(&str, &str)]) -> String {
    values
        .iter()
        .fold(template.to_string(), |text, (name, value)| {
            text.replace(&format!("{{{name}}}"), value)
        })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Each template of a language, beside the places it must hold.
    fn templates(words: &Words) -> Vec<(&'static str, &'static [&'static str])> {
        vec![
            (words.tip_counted, &["{waiting}", "{working}"]),
            (words.tip_downloading, &["{progress}"]),
            (words.title_unproven, &["{address}"]),
            (words.detail_unproven, &["{listening}"]),
            (words.downloading_already, &["{progress}"]),
            (words.newest, &["{current}"]),
            (words.could_not_look, &["{error}"]),
            (words.newer_body, &["{version}", "{current}"]),
            (words.could_not_install, &["{error}"]),
        ]
    }

    #[test]
    fn both_languages_fill_the_same_places_and_say_each_phrase() {
        for lang in [Lang::En, Lang::Tr] {
            for (template, places) in templates(lang.words()) {
                for place in places {
                    assert!(
                        template.contains(place),
                        "{lang:?}: {template} lacks {place}"
                    );
                }
                let filled = fill(
                    template,
                    &places
                        .iter()
                        .map(|place| (place.trim_matches(['{', '}']), "x"))
                        .collect::<Vec<_>>(),
                );
                assert!(!filled.contains('{'), "{lang:?}: {filled}");
                assert!(!filled.contains("  "), "{lang:?}: {filled}");
            }
        }
        assert_eq!(Lang::of("tr"), Lang::Tr);
        assert_eq!(Lang::of("en"), Lang::En);
        assert_eq!(Lang::of("de"), Lang::En);
        assert_eq!(
            fill(
                Lang::Tr.words().tip_counted,
                &[("waiting", "2"), ("working", "1")]
            ),
            "Dot · 2 sizi bekliyor · 1 çalışıyor"
        );
    }
}
