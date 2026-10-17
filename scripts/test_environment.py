"""OS bootstrap variables retained by isolated application test environments."""
import os


def system_environment():
    # Windows OpenSSL needs SystemRoot to initialize its system provider.
    # Keep application settings, credentials and proxy overrides out of fixtures.
    # Match CI's encoding for child processes as well as the parent test.
    return {'PYTHONUTF8':'1', **{key:os.environ[key] for key in ('SystemRoot','WINDIR') if key in os.environ}}
