"""tools/make_release.sh packs only the files git tracks, as they are in the folder: an edited file goes in with
its edits and a newly added one goes in, but untracked notes, ignored folders and a deleted file don't, and the
archives carry no Mac file attributes. Made in a throwaway copy of the repo; nothing is sent anywhere.
Every link to BitCue's repo (the install command, the badges, the issue links) names its one home on GitHub, and
every parts list asks for industrial double-sided tape (3M VHB) for the housing's plate and offers the table
stand on rubber feet. The docs folder works as the website on its own: it has a front page, and its pages link
only inside it or to full addresses."""
import os, re, shutil, subprocess, tarfile, tempfile, zipfile
from common import ROOT, check

HOME = "journeyonlabs"

if not (shutil.which("git") and shutil.which("zip")):
    print("  skipped - making a release (needs git and zip)")
    raise SystemExit(0)

print("every link to BitCue's repo names its home on GitHub")
owners = {}
tracked = subprocess.run(["git", "-C", ROOT, "ls-files", "-z"], stdout=subprocess.PIPE, check=True).stdout.decode()
for name in filter(None, tracked.split("\0")):
    path = os.path.join(ROOT, name)
    if os.path.isfile(path):
        with open(path, "rb") as fh:
            text = fh.read().decode("utf-8", "ignore")
        for owner in re.findall(r"github(?:\.com|/v/release)/([A-Za-z0-9-]+)/bitcue\b", text):
            owners.setdefault(owner, set()).add(name)
check(HOME in owners, "the README names %s/bitcue" % HOME)
check(set(owners) == {HOME}, "no other home: %s" % {o: sorted(f) for o, f in owners.items() if o != HOME})

print("no price next to an Amazon link (the Associates rules allow prices only where Amazon serves them)")
with open(os.path.join(ROOT, "docs", "buy.md"), encoding="utf-8") as fh:
    priced = [line.strip()[:70] for line in fh if "[amazon-" in line and re.search(r"\$\s?\d", line)]
check(not priced, "docs/buy.md has no Amazon link on a line with a price %s" % priced)

print("every parts list asks for industrial double-sided tape for the plate")
for name in ("README.md", "docs/buy.md", "hardware/README.md"):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        text = " ".join(fh.read().lower().split())
    check("industrial double-sided tape" in text and "3m vhb" in text and "comes with the z1" in text,
          "%s names 3M VHB and warns about the Z1's own tape" % name)
    check("table stand" in text and "rubber feet" in text, "%s offers the table stand on rubber feet" % name)

print("the docs folder works as the website")
docs = os.path.join(ROOT, "docs")
check(os.path.isfile(os.path.join(docs, "index.md")), "docs/index.md is the website's front page")
for name in sorted(os.listdir(docs)):
    if name.endswith(".md"):
        with open(os.path.join(docs, name), encoding="utf-8") as fh:
            text = fh.read()
        outside = [a or b for a, b in re.findall(r"\]\((\.\./[^)]*)\)|^\[[^\]]+\]:\s*(\.\./\S*)", text, re.M)]
        check(not outside, "docs/%s links only inside docs/ or to full addresses %s" % (name, outside))

work = tempfile.mkdtemp()
repo = os.path.join(work, "repo")
os.makedirs(repo)
archive = subprocess.run(["git", "-C", ROOT, "archive", "HEAD"], stdout=subprocess.PIPE, check=True).stdout
subprocess.run(["tar", "-x", "-C", repo], input=archive, check=True)
shutil.copy(os.path.join(ROOT, "tools", "make_release.sh"), os.path.join(repo, "tools", "make_release.sh"))


def git(*args):
    subprocess.run(["git", "-C", repo, "-c", "user.name=Test", "-c", "user.email=test@example.com"] + list(args),
                   stdout=subprocess.DEVNULL, check=True)


def write(path, text):
    full = os.path.join(repo, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as fh:
        fh.write(text)


git("init", "-q")
git("add", "-A")
git("commit", "-q", "-m", "base")
with open(os.path.join(repo, "README.md"), "a") as fh:
    fh.write("\nrelease-test-marker\n")
write("notes.local.md", "private notes\n")
write(".venv/lib/thing.py", "x = 1\n")
write("dist/old.txt", "old\n")
os.remove(os.path.join(repo, "NOTICE.md"))
write("docs/new-picture.txt", "new\n")
git("add", "docs/new-picture.txt")

print("a release from a folder with untracked, ignored, edited, deleted and new files")
run = subprocess.run(["bash", "tools/make_release.sh"], cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                     universal_newlines=True)
check(run.returncode == 0, "make_release.sh finished (%s)" % run.stdout.strip().splitlines()[-1:])
dist = os.path.join(repo, "dist")
zips = [f for f in os.listdir(dist) if f.endswith(".zip")]
check(len(zips) == 1 and os.path.exists(os.path.join(dist, "z1screen.tar.gz")), "both files in dist/: %s" % sorted(os.listdir(dist)))
with zipfile.ZipFile(os.path.join(dist, zips[0])) as z:
    top = zips[0][:-4] + "/"
    in_zip = {n[len(top):] for n in z.namelist() if n.startswith(top) and not n.endswith("/")}
    readme = z.read(top + "README.md").decode()
with tarfile.open(os.path.join(dist, "z1screen.tar.gz")) as t:
    members = [m for m in t.getmembers() if m.isfile()]
    in_tar = {m.name[len("z1screen/"):] for m in members}
    attrs = [m.name for m in members if any("xattr" in k for k in m.pax_headers)]

check(in_zip == in_tar, "the zip and the tar.gz hold the same files")
check("z1screen/__init__.py" in in_zip and "Install on the Pi.command" in in_zip, "the kit's files are in, names with spaces too")
check("release-test-marker" in readme, "an edited file goes in as it is in the folder")
check("docs/new-picture.txt" in in_zip, "a file added to git goes in")
check("notes.local.md" not in in_zip, "an untracked file stays out")
check(not any(n.startswith((".venv/", "dist/")) for n in in_zip), "ignored and untracked folders stay out")
check("NOTICE.md" not in in_zip, "a tracked file deleted in the folder is left out, without an error")
check(not any(os.path.basename(n).startswith("._") for n in in_zip) and not attrs, "no Mac attributes or ._ files")

print("outside a git checkout it stops")
plain = os.path.join(work, "plain")
os.makedirs(os.path.join(plain, "tools"))
os.makedirs(os.path.join(plain, "z1screen"))
shutil.copy(os.path.join(repo, "tools", "make_release.sh"), os.path.join(plain, "tools"))
shutil.copy(os.path.join(repo, "z1screen", "__init__.py"), os.path.join(plain, "z1screen"))
run = subprocess.run(["bash", "tools/make_release.sh"], cwd=plain, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                     universal_newlines=True, env=dict(os.environ, GIT_CEILING_DIRECTORIES=work))
check(run.returncode != 0 and "git checkout" in run.stdout and not os.path.exists(os.path.join(plain, "dist")),
      "it says it needs a git checkout and makes nothing")
shutil.rmtree(work)
print("release checks passed")
