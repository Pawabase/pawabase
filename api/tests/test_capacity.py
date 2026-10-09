from pathlib import Path

from app.capacity import directory_bytes, process_memory_bytes

ENV = "/platform/v1/envs/development"


def test_the_process_reports_its_memory():
    assert process_memory_bytes() > 1_000_000


def test_directory_bytes_counts_and_stops(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "one").write_bytes(b"x" * 100)
    (tmp_path / "two").write_bytes(b"y" * 50)
    assert directory_bytes(tmp_path) == (150, False)
    for index in range(5):
        (tmp_path / f"more{index}").write_bytes(b"z")
    size, truncated = directory_bytes(tmp_path, limit=3)
    assert truncated is True and size < 160


async def test_capacity_reports_the_process_and_each_environment(api):
    await api.studio.post(
        f"{ENV}/resources",
        json={"name": "notes", "fields": [{"name": "title", "type": "string"}]},
    )
    await api.studio.post(f"{ENV}/resources/notes/migrate")
    await api.studio.post(f"{ENV}/keys", json={"name": "server", "role": "secret"})
    report = await api.studio.get("/platform/v1/capacity")
    assert report["process"]["memory_bytes"] > 1_000_000
    assert report["process"]["uptime_seconds"] >= 0
    development = next(e for e in report["environments"] if e["name"] == "development")
    assert development["resources"] == 1
    assert development["api_keys"] >= 1
    assert development["database"]["dialect"] == "sqlite"
    assert development["database"]["bytes"] > 0
    assert development["storage"]["driver"] == "local"


async def test_local_storage_bytes_are_counted(api):
    root = Path(api.settings.storage_root) / "development" / "files"
    root.mkdir(parents=True)
    (root / "report.pdf").write_bytes(b"p" * 2048)
    report = await api.studio.get("/platform/v1/capacity")
    development = next(e for e in report["environments"] if e["name"] == "development")
    assert development["storage"]["bytes"] == 2048
    assert development["storage"]["truncated"] is False


async def test_one_broken_environment_does_not_hide_the_others(api, monkeypatch):
    await api.studio.post("/platform/v1/envs", json={"name": "staging"})
    monkeypatch.setenv("STAGING_DATA_URL", "postgres://nobody:x@127.0.0.1:1/none")
    api.platform.envs.forget("staging")
    report = await api.studio.get("/platform/v1/capacity")
    by_name = {e["name"]: e for e in report["environments"]}
    assert "error" in by_name["staging"]
    assert by_name["development"]["database"]["bytes"] is not None
