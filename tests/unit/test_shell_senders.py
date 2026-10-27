"""The upload and publish tools the shell guard knows by name (item 725, the outside review of 2026-10-27): a package
published or an image pushed is a public post, asked in every preset; a file uploaded asks once the run read outside
content, as ``git push`` and ``scp`` do; and the same tools reading ask nothing. Nothing here runs: each line is only
judged."""

from pathlib import Path

import pytest

from dotagent.policy.engine import decide
from dotagent.policy.types import ActionInfo, Flag
from dotagent.tools.shell import ShellArgs, _classify_shell

PUBLISHES: tuple[str, ...] = (
    "npm publish",
    "npm --registry https://registry.example publish --access public",
    "pnpm publish",
    "yarn npm publish",
    "bun publish",
    "cargo publish --allow-dirty",
    "twine upload dist/*",
    # Through a runner, judged by the tool it runs (it was an everyday line before item 725).
    "uv run twine upload dist/*",
    "uv publish",
    "poetry publish --build",
    "flit publish",
    "hatch publish",
    "gem push dot-1.0.gem",
    "docker push owner/image:1",
    "docker --context remote image push owner/image",
    "podman push owner/image",
    "docker buildx build --push -t owner/image .",
    # The spellings the independent review of the first push found (each the tool's own).
    "npm pub",
    "gem pu dot-1.0.gem",
    "docker buildx build --push=true -t owner/image .",
    "docker buildx build --output type=registry -t owner/image .",
    "docker buildx build -o type=image,push=true -t owner/image .",
    "docker compose push",
    "docker manifest push owner/image",
    "docker compose build --push",
    "docker buildx build --output=type=registry -t owner/image .",
)
UPLOADS: tuple[str, ...] = (
    "aws s3 cp notes.txt s3://bucket/notes.txt",
    "aws --profile work s3 sync . s3://bucket/",
    "aws s3api put-object --bucket b --key k --body notes.txt",
    "gsutil cp notes.txt gs://bucket/",
    "gcloud storage rsync . gs://bucket",
    "az storage blob upload --file notes.txt --container-name c",
    "rclone copy notes.txt remote:bucket",
    "rclone --config c.conf sync . remote:bucket",
    "hg push",
    "svn commit -m update",
    "croc send notes.txt",
    "wormhole send notes.txt",
    "lftp -e 'put notes.txt' ftp://anonymous@ftp.example",
    "smbclient //host/share",
    "tftp host",
    # An option between a service and its verb, aliases, and verbs the first table did not name (the independent
    # review of the first push): a verb that is not one that only reads sends.
    "aws s3 --profile prod cp notes.txt s3://bucket/notes.txt",
    "aws s3 --region us-east-1 cp notes.txt s3://bucket/notes.txt",
    "aws s3api --region us-east-1 put-object --bucket b --key k --body notes.txt",
    "gcloud storage --project p cp notes.txt gs://bucket",
    "az storage fs file upload -s notes.txt -p p -f fs",
    "az storage azcopy blob upload -c c -s notes.txt",
    "rclone rcat remote:bucket/notes.txt",
    "rclone serve http /",
    "hg pus",
    "wormhole tx notes.txt",
    "svn copy notes.txt https://svn.example/repo/notes.txt -m add",
    # A copy in either direction: which side is remote is not told (docs/safety.md says so).
    "aws s3 cp s3://bucket/notes.txt .",
    # A link anyone can open to a private file sends it (the pre-push review of the third push).
    "rclone link remote:private/taxes.pdf",
    "gsutil signurl key.json gs://bucket/secret",
    "aws s3 presign s3://bucket/secret",
    # Azure's own, and any verb of az storage that is no read (the pre-push review of the fourth push).
    "az storage blob generate-sas --account-name acct -c private -n taxes.pdf --permissions r --full-uri",
    "az storage container generate-sas --account-name acct -n private",
    "az storage blob set-tier -c c -n taxes.pdf --tier Hot",
    # An option's value that is a reading verb is no verb (the review of the fifth push).
    "az storage container set-permission -n list --public-access container",
    # Every command of a cloud storage service, its reads too (the owner's choice after the review of the sixth push:
    # a list of reads let through each verb a group hid).
    "gcloud storage objects update gs://b/taxes.pdf --add-acl-grant=entity=AllUsers,role=READER",
    "gcloud storage buckets add-iam-policy-binding gs://b --member=allUsers --role=roles/storage.objectViewer",
    "aws s3 ls s3://bucket/",
    "aws --profile work s3 ls",
    "gcloud storage ls gs://bucket",
    "az storage blob list -c c",
    "aws s3 --profile work ls",
    "az storage blob download -c c -n taxes.pdf -f taxes.pdf",
    "az storage account show -n acct",
    # gcloud's release tracks before the service (the review of the seventh push).
    "gcloud beta storage cp secrets.tar gs://bucket",
    "gcloud alpha storage buckets add-iam-policy-binding gs://b --member=allUsers --role=roles/storage.objectViewer",
    "gcloud beta --project p storage cp notes.txt gs://bucket",
    # The service among any of the words (the review of the eighth push), so an unknown option past the track too; a
    # value named like a service asks as well, the safe side.
    "gcloud alpha --billing-project p storage cp secrets.tar gs://bucket",
    "az group create -n storage -l westus",
)
READS: tuple[str, ...] = (
    "gsutil ls gs://bucket",
    "rclone ls remote:bucket",
    "npm view dotagent",
    "cargo build",
    "docker pull owner/image",
    "gem list",
    "hg pull",
    "svn update",
    "twine check dist/*",
    "croc --help",
    # A verb that only stands among the words of another command (the pre-push review of the first push).
    "docker run --rm image echo push",
    "hg log -k push",
    "npm view publish",
    "wormhole receive",
    "svn copy notes.txt copy.txt",
    "aws ec2 describe-instances",
    "gcloud beta compute instances list",
    # The options of a build are read after its verb alone (the pre-push review of the second push).
    "docker run -e PUSH=true app",
    "docker run -e CACHE_TYPE=registry app",
    "docker run --rm ci-image ./release.sh --push",
    "docker buildx build --push=false -t owner/image .",
)


def _info(line: str, workspace: Path) -> ActionInfo:
    return _classify_shell(ShellArgs(command=line), workspace, (), ())


def _asked(info: ActionInfo, tainted: bool) -> bool:
    return decide(info, "task", tainted, "balanced", [], None).verdict == "ask"


@pytest.mark.parametrize("line", PUBLISHES)
def test_a_package_published_is_a_public_post_asked_in_a_clean_run_too(line: str, tmp_path: Path) -> None:
    info: ActionInfo = _info(line, tmp_path)
    expected: tuple[Flag, ...] = ("public_post",)
    assert (info.risk, info.flags) == ("medium", expected), info.summary
    assert _asked(info, tainted=False) and _asked(info, tainted=True)


@pytest.mark.parametrize("line", UPLOADS)
def test_a_file_uploaded_asks_once_the_run_read_outside_content(line: str, tmp_path: Path) -> None:
    info: ActionInfo = _info(line, tmp_path)
    expected: tuple[Flag, ...] = ("upload",)
    assert (info.risk, info.flags) == ("medium", expected), info.summary
    assert _asked(info, tainted=True)


@pytest.mark.parametrize("line", READS)
def test_the_same_tools_reading_ask_nothing(line: str, tmp_path: Path) -> None:
    info: ActionInfo = _info(line, tmp_path)
    assert "upload" not in info.flags and "public_post" not in info.flags, info.summary
    assert not _asked(info, tainted=True)
