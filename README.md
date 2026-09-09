# Apostrophe — personal fork with rich editing and typewriter sounds

> Personal fork of [Apostrophe](https://gitlab.gnome.org/World/apostrophe) on
> top of upstream v3.4, branch `feat/rich-editing`. Not affiliated with the
> Apostrophe maintainers. No warranty, no support. Commits are atomic per
> feature so anything can be cherry-picked.

> **All modifications in this fork were made by LLM agents** (several
> models and coding agents) under my direction, on top of the upstream code.
>
> <sub>For that very reason I am not submitting them upstream: right now I
> don't have the time to review every change and send it the way it should be
> sent, and I don't want to add noise to the projects or their communities. I
> am only after tools that fit my own workflow better, and I leave them public
> here in case any of these changes inspires or helps someone else.</sub>

> ⚠️ **This fork keeps the upstream name, app id (`org.gnome.gitlab.somas.Apostrophe`) and icon.**
> Installed, it **replaces** the official Apostrophe and shows up as "Apostrophe" in
> your application list. If something breaks, **report it to this
> repository**, not to upstream: the changes here are downstream-only and the
> upstream maintainers should not have to triage them. If you can reproduce
> the problem on the official build, report it there instead.

## What's different from upstream

- **Rich Markdown editing mode** (*View → Rich Editing*): headings,
  emphasis, links and lists are rendered in place inside the editor while
  the document stays plain Markdown on disk; formatting refreshes as you
  type. The rich editor is a distinct component so the classic editor is
  untouched.
- **Typewriter sounds**, off by default: a soft mechanical click per
  keystroke, with selectable profiles (*Typewriter*, *Classic*, *Electric*),
  volume and preview in preferences. Samples are played from a pool of
  natural, randomised variations instead of one repeated click.

## Building and installing this fork

Apostrophe is a Python + GTK4 application built with Meson; it needs
`pandoc` at runtime. On Fedora:

```sh
sudo dnf install meson pandoc gtk4-devel libadwaita-devel webkitgtk6.0-devel \
    libspelling-devel gtksourceview5-devel python3-gobject python3-regex \
    python3-levenshtein python3-enchant python3-pypandoc python3-cairo
meson setup _build --prefix="$HOME/.local" -Dprofile=default
ninja -C _build install
apostrophe
```

The typewriter sound samples live in `data/sounds/` and are installed with
the application. Installing into `~/.local` shadows the system Apostrophe
for your user only; `--prefix=/usr` replaces it system-wide. A Flatpak
manifest is kept in `build-aux/flatpak/` for local builds; nothing is
published.

## Credits and license

This is a downstream fork of **[Apostrophe](https://gitlab.gnome.org/World/apostrophe)**. All the
credit for the application itself goes to its authors and contributors
(Wolf Vollprecht, Manuel Genovés and contributors); this repository only adds the changes listed above. The upstream
project is the place to get the official application; nothing here is
published on Flathub or in any distribution.

The code inherits the upstream license, **GPL-3.0-or-later** (see `COPYING`). Original
copyright headers are preserved in every file; the fork's changes are in the
git history of this repository.

## Reporting issues

Report problems with this fork **here**, not upstream. If you can reproduce
the problem on the official build, report it there instead.
