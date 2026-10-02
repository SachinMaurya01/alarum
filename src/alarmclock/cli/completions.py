"""Shell completion scripts: `alarm completions <shell>`."""

from __future__ import annotations

COMMANDS = (
    "add list remove enable disable next run snooze dismiss edit "
    "daemon config doctor completions"
)

_BASH = """\
# alarm bash completion — install: alarm completions bash >> ~/.bash_completion
_alarm_complete() {
    local cur="${COMP_WORDS[COMP_CWORD]}"
    local cmds="COMMANDS"
    if [[ $COMP_CWORD -eq 1 ]]; then
        COMPREPLY=($(compgen -W "$cmds" -- "$cur"))
        return 0
    fi
    case "${COMP_WORDS[1]}" in
        daemon) COMPREPLY=($(compgen -W "start stop status" -- "$cur")) ;;
        config) COMPREPLY=($(compgen -W "show set" -- "$cur")) ;;
        completions) COMPREPLY=($(compgen -W "bash zsh fish" -- "$cur")) ;;
        *) COMPREPLY=($(compgen -W "--json --quiet --verbose --debug --data-file --config" -- "$cur")) ;;
    esac
}
complete -F _alarm_complete alarm
""".replace("COMMANDS", COMMANDS)

_ZSH = """\
# alarm zsh completion — install: alarm completions zsh > ~/.zsh/completions/_alarm
#compdef alarm
_alarm() {
    local -a cmds daemon_cmds
    cmds=(COMMANDS_PAIRS)
    _arguments -C '1:command:->cmd' '*:: :->args'
    case $state in
        cmd) _describe 'command' cmds ;;
        args) case $words[2] in
            daemon) _describe 'daemon' '(start stop status)' ;;
            config) _describe 'config' '(show set)' ;;
            completions) _describe 'shell' '(bash zsh fish)' ;;
        esac ;;
    esac
}
_alarm
""".replace(
    "COMMANDS_PAIRS",
    " ".join(f"'{c}:{c}'" for c in COMMANDS.split()),
)

_FISH = """\
# alarm fish completion — install: alarm completions fish > ~/.config/fish/completions/alarm.fish
set -l cmds COMMANDS
complete -c alarm -f -n '__fish_use_subcommand' -a "$cmds"
complete -c alarm -f -n '__fish_seen_subcommand_from daemon' -a 'start stop status'
complete -c alarm -f -n '__fish_seen_subcommand_from config' -a 'show set'
complete -c alarm -f -n '__fish_seen_subcommand_from completions' -a 'bash zsh fish'
""".replace("COMMANDS", COMMANDS)


def completion_script(shell: str) -> str:
    scripts = {"bash": _BASH, "zsh": _ZSH, "fish": _FISH}
    try:
        return scripts[shell.lower()]
    except KeyError:
        from alarmclock.errors import InvalidInput

        raise InvalidInput(
            f"Unknown shell {shell!r}; choose bash, zsh or fish"
        ) from None
