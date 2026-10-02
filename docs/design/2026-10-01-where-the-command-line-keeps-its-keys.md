# Where the command line keeps its keys

**Status:** proposed. **Slice 1 landed on 2026-10-01:** the measurements are in
`guides/configuration.md`, under *What each fence hides*, which is the copy to keep
current; the table below is the one this argument was made from. The loader is not
built.
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
being secret. With a default under the home folder, such as
`~/.config/kingfisher/keys.yaml`, the macOS fence hides it. Neither Linux fence grants
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

## Open, and deliberately not decided yet

- **The keys file's default path, and its setting's name.** `~/.config/kingfisher/keys.yaml`
  and `KINGFISHER_KEYS_FILE` are placeholders.
- **What `doctor` says about it.** It could name, per endpoint, where its key came
  from, and say when the keys file sits somewhere the running fence lets the shell
  read. That would be a report, consistent with no enforcement, but it is a new check
  and its wording is a choice.
- **Whether `.env` keeps non-secret settings.** The owner's remark was about API keys.
  Moving every `KINGFISHER_*` setting into YAML would be a larger change, with its own
  argument.
- **A key in the starting environment** (note 1 above) stays readable on macOS whatever
  this decides. That is a property of the operating system rather than of kingfisher,
  and it belongs in the guide, not in code.

## What this does not do

It adds no sandbox rule, refuses no key file for where it sits, and does not change
what a deployment wiring kingfisher in code does. Both cases in #628 stay where they
are.

## Plan, if this moves

Each slice independent and green, a pull request off `main`, test first.

1. **The measurements, into `docs/guides/configuration.md`.** The table above, as what
   each fence hides, with the advice to keep keys under the home folder on macOS. It
   depends on nothing here and is worth landing even if the rest never does.
2. **The loader.** `models.yaml` accepts `key:` naming an entry in the keys file. The
   keys file is found through the setting. "Exactly one of `key` or `key_env`" is
   refused as the file is read. An endpoint whose entry is missing is dropped, with the
   same `MissingCredentialsWarning` that a missing `key_env` gets, naming the keys file
   and the entry.
3. **`seed` and the examples.** `models.yaml.example` shows `key:`, a commented
   `keys.yaml.example` says where it goes, and `.env.example` stops listing key lines.
4. **`doctor`**, if the open question above settles on a check.
