"""What Dot makes for a new account (item 740): a password with each kind of character and no two alike in a run of
them, and the website its login is kept for, which is one a login would be filled on again (https, or http on this Mac
itself) and nothing else."""

from dotagent.tools.browser import new_password, website_of


def test_a_new_password_holds_each_kind_of_character_and_none_repeats_across_many() -> None:
    made: set[str] = {new_password() for _ in range(200)}
    assert len(made) == 200
    for password in made:
        assert len(password) == 20
        assert any(char.islower() for char in password) and any(char.isupper() for char in password)
        assert any(char.isdigit() for char in password) and any(char in "-_.!@#%" for char in password)
        # Letters and digits that read alike (l, 1, O, 0, I) are left out, for a password the owner may read aloud.
        assert not set(password) & set("l1O0I")


def test_the_website_of_a_new_login_is_one_it_would_be_filled_on_again() -> None:
    assert website_of("https://Shop.Example.com/signup?x=1") == "https://shop.example.com"
    assert website_of("https://shop.example.com:8443/signup") == "https://shop.example.com:8443"
    assert website_of("http://127.0.0.1:8080/signup") == "http://127.0.0.1:8080"
    assert website_of("http://shop.example.com/signup") is None
    assert website_of("about:blank") is None
    assert website_of("") is None
