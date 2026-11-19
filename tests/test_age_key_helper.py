import os
import subprocess
import tempfile
import unittest
from pathlib import Path

HELPER = Path(__file__).resolve().parent.parent / "scripts" / "age_key_helper.sh"
SECRET = "AGE-SECRET-KEY-1TESTONLYNOTREAL"


def fake_bin(base: Path, rota: str) -> Path:
    b = base / "bin"
    b.mkdir()
    (b / "findmnt").write_text("#!/bin/sh\necho /dev/fakedisk\n")
    (b / "lsblk").write_text(f'#!/bin/sh\necho "disk {rota}"\n')
    # age: only the `-p -o OUT FILE` form is used; the stub records that it saw the secret and writes an age-looking file.
    (b / "age").write_text(
        "#!/bin/sh\nout=''; while [ $# -gt 0 ]; do case $1 in -o) out=$2; shift 2;; -p) shift;; *) in=$1; shift;; esac; done\n"
        f"grep -q {SECRET} \"$in\" && printf 'age-encryption.org/v1\\n-> scrypt stub\\n' > \"$out\"\n"
    )
    (b / "age-keygen").write_text(
        f"#!/bin/sh\nwhile [ $# -gt 0 ]; do case $1 in -o) out=$2; shift 2;; *) shift;; esac; done\n"
        f"echo '{SECRET}' > \"$out\"; echo 'Public key: age1testrecipientabc' >&2\n"
    )
    for f in b.iterdir():
        f.chmod(0o755)
    return b


def run(args, rota="1", pre=None):
    with tempfile.TemporaryDirectory() as t:
        base = Path(t)
        bin_ = fake_bin(base, rota)
        run_dir = base / "run"
        run_dir.mkdir()
        keys = base / "keys"
        if pre:
            pre(base, keys)
        env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "XDG_RUNTIME_DIR": str(run_dir)}
        p = subprocess.run(["bash", str(HELPER), *[a.replace("@KEYS", str(keys)).replace("@BASE", str(base)) for a in args]],
                           env=env, capture_output=True, text=True, check=False)
        files = sorted(x.name for x in keys.iterdir()) if keys.exists() else []
        modes = {x.name: oct(x.stat().st_mode & 0o777) for x in keys.iterdir()} if keys.exists() else {}
        leftover = list(run_dir.iterdir())
        pub = (keys / "recovery-recipient.pub").read_text().strip() if (keys / "recovery-recipient.pub").exists() else ""
        return p, files, modes, leftover, pub


class Helper(unittest.TestCase):
    def test_recovery_key_writes_protected_secret_and_public_recipient(self):
        p, files, modes, leftover, pub = run(["recovery-key", "--dir", "@KEYS"])
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(files, ["recovery-key.age", "recovery-recipient.pub"])
        self.assertEqual(modes["recovery-key.age"], "0o600")
        self.assertEqual(pub, "age1testrecipientabc")
        self.assertIn("age1testrecipientabc", p.stdout)
        self.assertNotIn(SECRET, p.stdout + p.stderr)  # the secret key is never printed
        self.assertEqual(leftover, [])  # the plaintext temp file was removed

    def test_protect_encrypts_an_existing_identity(self):
        def pre(base, _keys):
            (base / "ident.txt").write_text(SECRET + "\n")

        p, files, _m, _l, _pub = run(["protect", "@BASE/ident.txt", "--dir", "@KEYS"], pre=pre)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(files, ["ident.txt.passphrase"])

    def test_refuses_to_overwrite(self):
        def pre(_base, keys):
            keys.mkdir()
            (keys / "recovery-key.age").write_text("existing")

        p, files, _m, _l, _pub = run(["recovery-key", "--dir", "@KEYS"], pre=pre)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("refusing to overwrite", p.stderr)
        self.assertEqual(files, ["recovery-key.age"])

    def test_refuses_flash_unless_allowed(self):
        p, files, *_ = run(["recovery-key", "--dir", "@KEYS"], rota="0")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("spinning disk", p.stderr)
        self.assertEqual(files, [])
        p, files, *_ = run(["recovery-key", "--dir", "@KEYS", "--allow-flash"], rota="0")
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_bad_usage(self):
        self.assertNotEqual(run(["bogus"])[0].returncode, 0)
        self.assertNotEqual(run(["protect"])[0].returncode, 0)
        self.assertNotEqual(run(["protect", "@BASE/missing", "--dir", "@KEYS"])[0].returncode, 0)


if __name__ == "__main__":
    unittest.main()
