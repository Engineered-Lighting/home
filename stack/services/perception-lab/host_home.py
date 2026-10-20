"""The Jetson service account's home directory.

Deployments set JETSON_HOME in the unit's private EnvironmentFile (see
deploy/README.md); every Jetson path in the Lab is built from it. The repo
carries only the placeholder, so nothing here names the real account.
"""
import os

PLACEHOLDER = '/home/jetson-user'
JETSON_HOME = os.environ.get('JETSON_HOME', PLACEHOLDER).rstrip('/') or PLACEHOLDER
