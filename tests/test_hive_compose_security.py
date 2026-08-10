import re
from pathlib import Path


COMPOSE_FILE = Path(__file__).parents[1] / "hive" / "docker-compose.yaml"


def test_hive_images_are_pinned_by_digest():
    compose = COMPOSE_FILE.read_text()
    images = re.findall(r"^\s+image:\s+(\S+)$", compose, re.MULTILINE)

    assert images
    assert all(re.search(r"@sha256:[0-9a-f]{64}$", image) for image in images)


def test_hive_compose_has_no_docker_socket():
    compose = COMPOSE_FILE.read_text()

    assert "/var/run/docker.sock" not in compose
