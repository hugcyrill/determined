#!/usr/bin/env python3

"""
Read the artifacts generated by a particular commit of the environments repo,
parse out the various image tags, and update a yaml file with the appropriate
values (probably bumpenvs.yaml).

So if before running this script, the yaml file looked like this:

    some_image:
        old: a123
        new: b456

And if the newly built artifact for some_image is called c789, then the yaml
file will be updated to look like:

    some_image:
        old: b456
        new: c789

Usage: update-bumpenvs-yaml.py path/to/bumpenvs.yaml ENVIRONMENTS_COMMIT
"""

import collections
import os
import sys
from typing import Any, Dict

import requests
import yaml

USER = "determined-ai"
PROJECT = "environments"
BASE_URL = f"https://circleci.com/api/v1.1/project/github/{USER}/{PROJECT}"

JOB_SUFFIXES = [
    "tf1-cpu",
    "tf2-cpu",
    "tf25-cpu",
    "tf26-cpu",
    "tf1-gpu",
    "tf2-gpu",
    "tf25-gpu",
    "tf26-gpu",
]

EXPECT_JOBS = {
    "publish-cloud-images",
    *(f"build-and-publish-docker-{suffix}" for suffix in JOB_SUFFIXES),
}

PACKER_ARTIFACTS = {
    "packer-log",
}

DOCKER_ARTIFACTS = {f"publish-{suffix}" for suffix in JOB_SUFFIXES}

EXPECT_ARTIFACTS = PACKER_ARTIFACTS | DOCKER_ARTIFACTS


class Build:
    """A neatly parsed CircleCI build."""

    def __init__(self, data: Dict["str", Any]) -> None:
        self.build_num = data["build_num"]
        self.job_name = data["workflows"]["job_name"]

    def get_artifacts(self) -> Dict[str, str]:
        print(f"fetching artifacts for {self.job_name}", file=sys.stderr)
        url = f"{BASE_URL}/{self.build_num}/artifacts"
        req = requests.get(url)
        req.raise_for_status()

        artifacts = {}
        for meta in req.json():
            path = os.path.basename(meta["path"])
            print(f"fetching {path}", file=sys.stderr)
            req = requests.get(meta["url"])
            req.raise_for_status()
            artifacts[path] = req.content.decode("utf8")
        return artifacts


def get_all_builds(commit: str) -> Dict[str, Build]:
    # Get all the recent jobs.
    print("fetching recent jobs", file=sys.stderr)
    req = requests.get(BASE_URL, params={"limit": 50, "filter": "completed"})  # type: ignore
    req.raise_for_status()

    # Get all the build numbers matching this commit.
    builds = {}
    for build_meta in req.json():
        if build_meta["vcs_revision"] == commit:
            if build_meta["status"] != "success":
                print(
                    f"Job: {build_meta['workflows']['job_name']} "
                    f"build: {build_meta['build_num']} did not succeed."
                )
                continue

            build = Build(build_meta)
            builds[build.job_name] = build

    found = set(builds.keys())
    assert EXPECT_JOBS == found, f"expected jobs ({EXPECT_JOBS}) but found ({found})"

    return builds


def get_all_artifacts(builds: Dict[str, Build]) -> Dict[str, str]:
    artifacts = {}
    for b in builds.values():
        artifacts.update(b.get_artifacts())

    found = set(artifacts.keys())
    assert (
        EXPECT_ARTIFACTS == found
    ), f"expected artifacts ({EXPECT_ARTIFACTS}) but found ({found})"

    return artifacts


def parse_packer_log(packer_log: str) -> Dict[str, str]:
    """Parse the output of packer's -machine-readable format, strange though it may be."""
    out = {}

    lines = packer_log.strip().split("\n")
    fields = [line.split(",") for line in lines]
    # We only care about artifact lines with exactly six fields.
    ArtifactLine = collections.namedtuple(
        "ArtifactLine", "time builder linetype index msgtype val"
    )

    # We only care about artifact lines with exactly 6 fields.
    artifact_lines = [
        ArtifactLine(*f) for f in fields if len(f) == 6 and f[2] == "artifact"
    ]

    # Get the ami images, which should match lines like this one (line break for readability):
    #   1598642161,amazon-ebs,artifact,0,id,us-east-1:
    #       ami-04894a7352df9fdf9%!(PACKER_COMMA)us-west-2:ami-017627938fe327e4f
    ami_lines = [
        a
        for a in artifact_lines
        if a.builder.startswith("amazon-ebs") and a.msgtype == "id" and a.val
    ]
    assert (
        len(ami_lines) == 2
    ), f"expected two matching ami ids line but got: {ami_lines}"

    for ami_line in ami_lines:
        ami_fields = ami_line.val.split("%!(PACKER_COMMA)")
        for ami_field in ami_fields:
            region, ami = ami_field.split(":")
            name = region.replace("-", "_") + "_agent_ami"
            out[name] = ami

    # Get the GCP builder name by matching the build-id from a line like this one:
    #   1598642161,det-environments-06318c7,artifact,0,builder-id,packer.googlecompute
    gcp_builders = [
        a.builder
        for a in artifact_lines
        if a.msgtype == "builder-id" and a.val == "packer.googlecompute"
    ]
    # aws gov images do not have matching gcp environment images.
    if len(gcp_builders) == 0:
        return out
    assert len(gcp_builders) == 1, f"expected one gcp builder but got: {gcp_builders}"
    gcp_builder = gcp_builders[0]

    # Get the GCP artifact ID by matching a line like this one:
    #    1598642161,det-environments-06318c7,artifact,0,id,det-environments-06318c7
    gcp_ids = [
        a.val
        for a in artifact_lines
        if a.builder == gcp_builder and a.msgtype == "id" and a.val
    ]
    assert len(gcp_ids) == 1, f"expected one matching gcp id line but got: {gcp_ids}"
    out["gcp_env"] = gcp_ids[0]

    return out


def update_tag_for_image_type(subconf: Dict[str, str], new_tag: str) -> bool:
    if new_tag == subconf["new"]:
        return False

    subconf["old"] = subconf["new"]
    subconf["new"] = new_tag
    return True


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    path = sys.argv[1]
    commit = sys.argv[2]

    with open(path) as f:
        conf = yaml.safe_load(f)

    builds = get_all_builds(commit)
    artifacts = get_all_artifacts(builds)

    tag_list = [
        *(parse_packer_log(artifacts[artifact]) for artifact in PACKER_ARTIFACTS),
        *(yaml.safe_load(artifacts[artifact]) for artifact in DOCKER_ARTIFACTS),
    ]

    # Flatten tag_list dicts into one dict.
    new_tags = {k: v for d in tag_list for (k, v) in d.items()}

    saw_change = False
    for image_type, new_tag in new_tags.items():
        if image_type not in conf:
            conf[image_type] = {"new": new_tag}
            saw_change = True
        else:
            saw_change |= update_tag_for_image_type(conf[image_type], new_tag)

    if not saw_change:
        print(
            "No changes detected, did you use the wrong commit?  Or run this script twice?",
            file=sys.stderr,
        )
        sys.exit(1)

    with open(path, "w") as f:
        yaml.dump(conf, f, sort_keys=True)

    print(f"done, {path} has been updated", file=sys.stderr)
