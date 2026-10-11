# Where the command line keeps its keys

**Status:** proposed. **Slice 1 landed on 2026-10-01:** the measurements are in
`guides/configuration.md`, under *What each fence hides*, which is the copy to keep
current; the table below is the one this argument was made from. **The open
questions were settled on 2026-10-11**, under *Settled*. **Slices 2 and 3 landed
together the same day:** a keys file `KINGFISHER_MODEL_KEYS_FILE` names, and the
examples that suggest one. The `doctor` check is not built.
**Date:** 2026-10-01.
**Occasion:** removing the session key raised what else the agent's shell can read,
and the model API keys came first. Two agents measured it on macOS and read the
Linux fences; the owner then settled what kingfisher does and does not take on. This
is the part left to design: moving the command line's keys out of `.env` and into
YAML, without putting them somewhere the shell reads more easily than today.
**Cited by symbol, not by line.**

## Already decided

These were settled before this document, and it does not reopen them.

- **Kingfisher enforces nothing to keep API keys from the agent's shell.** It
  documents what each fence hides and leaves the deployment to decide, which is the
  same line it drew when it stopped signing what a session keeps.
- **Keys come from the side that runs kingfisher.** A deployment wiring kingfisher in
  code already hands them over itself: `Endpoint(api_key=...)` takes the key, from
  whatever secret store the deployment keeps, and no environment variable is read.
  So this proposal is about the one caller that has no such program -- the
  `kingfisher` command, which today reads keys from the environment that `.env` fills.
- **Real use runs on a remote backend.** Its shell runs on another machine and cannot
  see this one, so where the command line keeps its keys matters mostly for runs on a
  developer's own machine.

## What the agent's shell can read, measured on 2026-10-01

What decides whether a key is exposed is where the file holding it sits, and which
fence is running. On macOS this was measured with probes run through `backend_at`.
On Linux it was read from the fences' code; Docker Desktop has no Landlock, so it
could not be run here.

| | macOS `sandbox-exec` | bubblewrap | Landlock | No fence |
|---|---|---|---|---|
| The operator's home | hidden | hidden | hidden | readable |
| The workspace, `models.yaml` included | **readable** (written-to is refused) | hidden | hidden | readable |
| Anywhere else -- `/tmp`, `/Users/Shared`, `/opt` | **readable** | hidden | hidden | readable |
| `/etc`, `/usr` | readable | readable | readable | readable |
| Kingfisher's environment, as it started | **readable**¹ | hidden (own PID namespace, no `/proc`) | not settled² | readable |
| Keys `load_dotenv` put into kingfisher's environment after it started | not found by any probe | hidden | hidden | not found by any probe |
| The network | **open** | closed | **open** | open |

1. `ps` is refused only by accident, because the sandbox will not launch a setuid
   binary. The system call it uses, `KERN_PROCARGS2`, answered from the venv's own
   `python3` for 530 of 773 same-user processes. It returns each process's environment
   *as it started*, so a key exported in a shell profile, a launchd plist or a
   container's `env:` is readable, and one `load_dotenv` added afterwards is not.
2. Landlock grants `/proc`, and a sandboxed shell has read other processes' command
   lines through it. The kernel is expected to refuse `/proc/<pid>/environ` across a
   Landlock domain, but no test covers it.

"No fence" covers `KINGFISHER_SHELL_SANDBOX=off`, `external` with nothing outside
actually confining the shell, and a Linux host where neither fence can start, which
warns and runs. Issue #628 tracks that last case, and the macOS profile reading the
whole workspace, as matters of sessions reaching each other; they are not this
document's.

So today's `.env` is safer than it looks: it usually sits in a checkout under the home
folder, which `sandbox-exec` hides. The obvious move into YAML would undo that.

## The options

The same endpoint, written each way.

**A -- the key inline in `models.yaml`.**

```yaml
endpoints:
  minimax:
    api: anthropic
    base_url: https://api.minimaxi.com/anthropic
    api_key: sk-...
```

One file, nothing to wire. But `models.yaml` defaults to `<workspace>/models.yaml`,
which the macOS fence lets the shell read, so the key becomes *more* exposed than in
today's `.env`. It is also the file a deployment shares, reviews and copies between
hosts, and a secret in it travels too. `KINGFISHER_MODELS_FILE` can move it under the
home folder, which hides it on macOS. On Linux it must stay out of `/etc` and `/usr`,
which both fences grant.

**B -- a keys file of its own, which `models.yaml` names entries in.**

```yaml
# models.yaml -- shareable, no secrets
endpoints:
  minimax:
    api: anthropic
    base_url: https://api.minimaxi.com/anthropic
    key: minimax
```

```yaml
# keys.yaml -- found through a setting, kept outside the workspace
minimax: sk-...
```

`models.yaml` stays a file anyone may read, and the keys sit somewhere chosen for
being secret. Named under the home folder -- the examples suggest
`~/.config/kingfisher/keys.yaml` -- the macOS fence hides it. Neither Linux fence grants
the home, and a remote backend never sees the file. It costs a second file, and a
setting to find it.

**C -- the key in a file of its own, one per endpoint.**

```yaml
endpoints:
  minimax:
    api: anthropic
    base_url: https://api.minimaxi.com/anthropic
    key_file: /run/secrets/minimax
```

This is the shape Docker and Kubernetes already hand secrets over in, a file per
secret, and `/run` is granted by neither Linux fence. On a laptop it means one file
per provider.

**D -- leave `key_env` and `.env` as they are.** It is safe where `.env` sits under the
home folder and kingfisher is started from there, and only there.

## Recommendation

**B, keeping `key_env` beside it.** B gives the owner what was asked for -- keys in
YAML -- without moving them anywhere the macOS fence reads more easily than `.env`. It
also keeps `models.yaml` a file that can be shared. `key_env` stays because a container
or a CI job is handed secrets as environment variables, and taking that away would
make those deployments write a file to get back to where they are. C is a good fit for
containers, but it is an addition rather than the answer: it can come later as a third
spelling, if a deployment asks.

Each endpoint names exactly one of `key` or `key_env`. Both, or neither, is refused
as the YAML is read, as an unknown key is now. That is a rule about the file's shape,
not a guard on the key, so it is not the enforcement ruled out above.

## Settled

Put to the owner on 2026-10-11, one question at a time, each with the same endpoint
written every way.

**Where the keys file is.**

- **No default: the keys file is read only once a setting names it.** Everything but
  the workspace "defaults inside it or is off", so that a deployment's files are
  never somewhere its own configuration does not mention. A home-folder default would
  have been the first place kingfisher picked outside the workspace on its own, and a
  workspace default would put keys where the macOS fence lets the shell read them. The
  examples suggest `~/.config/kingfisher/keys.yaml`, written out, so the path is chosen
  and visible.
- **The setting is `KINGFISHER_MODEL_KEYS_FILE`.** `KINGFISHER_KEYS_FILE` read as a
  successor to the session key that had just been retired; this one says what it holds.
- **Only the setting names the file; `models.yaml` never does.** A path in it would be
  one machine's path in the file that is shared between machines, which is the reason
  for a second file.
- **Paths resolve like the other path settings:** `~` expanded, a relative path from
  the directory kingfisher was started in, where `.env` is read. Relative to the
  workspace would have pointed relative paths into what the macOS fence lets the shell
  read.

**What happens when it is not right.**

- **An endpoint names `key:` and no keys file is named:** that endpoint is dropped
  with `MissingCredentialsWarning`, naming the setting, and the others load -- as a
  missing `key_env` does. A machine without one provider set up still runs the rest.
- **The file is named and has no such entry:** dropped the same way, naming the file
  and the entry.
- **The named file is missing, unreadable, or not YAML:** kingfisher refuses to start
  and names the path, as `KINGFISHER_MODELS_FILE` does. A wrong path somebody wrote is
  a mistake to say at once, where a missing provider is a machine's ordinary state.

**What `seed` gives you.** No keys example file. `models.yaml.example` shows `key:`, and
`.env.example` shows the setting with a two-line sample of the file beside it. A
`keys.yaml.example` in the workspace would make renaming it in place the obvious next
step, which puts real keys where the shell reads them.

**What `doctor` reports.** A report each, nothing refused, and only in `doctor`, not at
every start -- the line `session files` already holds.

- **Where each endpoint's key came from,** in the `credentials` row:
  `minimax: keys file, entry "minimax"; openai: OPENAI_API_KEY`.
- **The keys file sitting where the agent's shell can read it,** worked out from the
  fence actually running, by the rules in *What each fence hides*: on macOS, outside
  the home folder or inside the workspace; on Linux, under a root the fence grants;
  with no fence, always. Warning about the workspace and staying quiet about `/tmp`
  would have read as `/tmp` being safe.
- **A key that was in the environment before kingfisher started,** on macOS and with
  no fence, because the shell can read a process's starting environment there; it
  suggests the keys file. For `doctor` to tell, the command line records which names
  its `.env` loading added. This reverses what this document first said, that note 1
  belonged in the guide rather than in code: a report is not a guard, and it is the
  one exposure a deployer cannot otherwise see.
- **Other users able to read the keys file** -- a mode wider than `600` -- with the
  `chmod 600` that would fix it.

**Only the keys move.** Every other `KINGFISHER_*` setting stays where it is. Moving them
all into YAML is a separate proposal, if it is still wanted: where such a file lives
when it names the workspace itself, which wins when it and the environment disagree,
and whether `.env` goes.

## What this does not do

It adds no sandbox rule, refuses no key file for where it sits, and does not change
what a deployment wiring kingfisher in code does. It moves no setting but the keys.
Both cases in #628 stay where they are.

## Plan, if this moves

Each slice independent and green, a pull request off `main`, test first.

1. **The measurements, into `docs/guides/configuration.md`.** The table above, as what
   each fence hides, with the advice to keep keys under the home folder on macOS. It
   depends on nothing here and is worth landing even if the rest never does.
2. **The loader.** `models.yaml` accepts `key:` naming an entry in the file
   `KINGFISHER_MODEL_KEYS_FILE` names, resolved as the other path settings are.
   "Exactly one of `key` or `key_env`" is refused as the file is read. No setting, or
   no entry, drops the endpoint with `MissingCredentialsWarning`, naming what was
   missing. A named file that is missing, unreadable or not YAML refuses to start.
3. **`seed` and the examples.** `models.yaml.example` shows `key:`; `.env.example`
   shows `KINGFISHER_MODEL_KEYS_FILE=~/.config/kingfisher/keys.yaml` with a sample of
   the file, and stops listing key lines. No keys example file is written.
4. **`doctor`.** The four reports under *Settled*. The command line starts recording
   which names `.env` added, which the starting-environment report needs.
