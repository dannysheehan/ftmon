#!/bin/sh
CUSTOM="/opt/ftmon"

BASE_DIR="$CUSTOM/base"
export BASE_DIR

HTML_DIR="$CUSTOM/html"
export HTML_DIR

CFG_DIR="$CUSTOM/cfg/Linux/cfg"

export CFG_DIR

LOG_DIR="$CUSTOM/logs"
export BASE_DIR

PERL="/usr/local/ActivePerl-5.6/bin/perl"
#PERL="/usr/bin/perl"
$PERL $BASE_DIR/bin/ftmon.pl -o $HTML_DIR -p $CFG_DIR -l $LOG_DIR -v 10
