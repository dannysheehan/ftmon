#!/bin/zsh
set -eu

label="org.ftmon.spike.reload"
domain="gui/$(id -u)"
spike_dir="${0:A:h}"
run_dir="$(mktemp -d /tmp/ftmon-launchagent-spike.XXXXXX)"
plist="$run_dir/$label.plist"
events="$run_dir/events.txt"
stdout="$run_dir/stdout.txt"
stderr="$run_dir/stderr.txt"

cleanup() {
    launchctl bootout "$domain/$label" 2>/dev/null || true
}
trap cleanup EXIT

buddy="/usr/libexec/PlistBuddy"
"$buddy" -c "Add :Label string $label" "$plist"
"$buddy" -c "Add :ProgramArguments array" "$plist"
"$buddy" -c "Add :ProgramArguments:0 string /usr/bin/python3" "$plist"
"$buddy" -c "Add :ProgramArguments:1 string $spike_dir/reload_target.py" "$plist"
"$buddy" -c "Add :ProgramArguments:2 string $events" "$plist"
"$buddy" -c "Add :RunAtLoad bool true" "$plist"
"$buddy" -c "Add :KeepAlive bool true" "$plist"
"$buddy" -c "Add :StandardOutPath string $stdout" "$plist"
"$buddy" -c "Add :StandardErrorPath string $stderr" "$plist"

plutil -lint "$plist"
launchctl bootstrap "$domain" "$plist"
sleep 1
launchctl print "$domain/$label" | sed -n '1,45p'
pid="$(launchctl print "$domain/$label" | awk '/pid =/ {print $3; exit}')"
kill -HUP "$pid"
sleep 1
printf '%s\n' "events:"
sed -n '1,20p' "$events"
printf '%s\n' "kickstart_pid_before=$pid"
launchctl kickstart -k "$domain/$label"
sleep 1
new_pid="$(launchctl print "$domain/$label" | awk '/pid =/ {print $3; exit}')"
printf '%s\n' "kickstart_pid_after=$new_pid"
printf '%s\n' "artifacts=$run_dir"
