from types import SimpleNamespace

from pawabase_server.identity import emails
from pawabase_server.identity.environment import AuthConfig
from pawabase_server.identity.hosted import clean_branding


class Sent:
    def __init__(self, templates):
        self.messages = []
        self.platform = SimpleNamespace(state=self.state)
        self._templates = templates

    async def state(self, env):
        return SimpleNamespace(mail_templates=self._templates)

    async def send_mail(self, env, **message):
        self.messages.append(message)


def config(**emails_config):
    return AuthConfig(
        env="development",
        project_name="Acme",
        emails=emails_config,
        branding={"name": "Acme Co", "logo_url": "https://cdn.test/logo.png", "primary": "#ff7049"},
    )


async def test_a_chosen_mail_template_is_sent_with_the_values_and_the_brand():
    accounts = Sent({"welcome": SimpleNamespace(status="published", html="<p>x</p>", text="x")})
    await emails.send(
        accounts, config(verify={"template": "welcome"}), "verify", "ada@example.com", link="https://x.test/v"
    )
    (message,) = accounts.messages
    assert message["template"] == "welcome"
    assert message["data"]["link"] == "https://x.test/v"
    assert message["data"]["email"] == "ada@example.com"
    assert message["data"]["brand"] == {
        "name": "Acme Co",
        "logo_url": "https://cdn.test/logo.png",
        "primary": "#ff7049",
    }


async def test_a_missing_or_unpublished_template_falls_back_to_the_default():
    for templates in ({}, {"welcome": SimpleNamespace(status="draft", html="", text="")}):
        accounts = Sent(templates)
        await emails.send(
            accounts, config(verify={"template": "welcome"}), "verify", "ada@example.com", link="https://x.test/v"
        )
        (message,) = accounts.messages
        assert message.get("template") is None
        assert "Confirm your email for Acme" in message["subject"]


async def test_without_a_choice_the_default_text_is_sent():
    accounts = Sent({})
    await emails.send(accounts, config(), "recovery", "ada@example.com", link="https://x.test/r")
    assert accounts.messages[0]["subject"] == "Reset your Acme password"


def test_the_logo_bucket_is_kept_with_the_branding():
    kept = clean_branding({"logo_url": "https://cdn.test/l.png", "logo_bucket": "brand", "primary": "#ff7049"})
    assert kept["logo_bucket"] == "brand" and kept["logo_url"] == "https://cdn.test/l.png"
