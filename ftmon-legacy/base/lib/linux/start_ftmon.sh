#!/bin/bash
#
# Startup script for the Fast Track Systems Monitor
#
# chkconfig: - 85 15
# description: FTMON is system monitoring library.  It is used to monitor \
#              just about anything.
# processname: ftmon
# pidfile: /var/run/ftmon.pid
# config: /var/ftmon/ftmon.cfg

# source function library
if [ -f /etc/init.d/functions ] ; then
  . /etc/init.d/functions
elif [ -f /etc/rc.d/init.d/functions ] ; then
  . /etc/rc.d/init.d/functions
else
  exit 0
fi

# Avoid using root's TMPDIR
unset TMPDIR

# Source networking configuration.
. /etc/sysconfig/network

if [ -f /etc/sysconfig/ftmon ]; then
   . /etc/sysconfig/ftmon
fi
export BASE_DIR

mkdir -p $HTML_DIR
mkdir -p $LOG_DIR

# Check that ftmon.conf exists.
[ -f $CFG_DIR/ftmon.cfg ] || exit 0

prog="ftmon"

start() {
	echo -n $"Starting $prog: "
        daemon $BASE_DIR/bin/ftmon.pl -o $HTML_DIR -p $CFG_DIR -l $LOG_DIR -g $PID_FILE -v $INTERVAL
	RETVAL=$?
	echo
	touch /var/lock/subsys/ftmon
	return $RETVAL
}

stop() {
	echo -n $"Stopping $prog: "
	killproc $BASE_DIR/bin/ftmon.pl
	RETVAL=$?
	echo
	rm -f /var/lock/subsys/ftmon
	return $RETVAL
}

restart(){
	stop
	start
}

condrestart(){
    [ -e /var/lock/subsys/ftmon ] && restart
    return 0
}

case "$1" in
  start)
	start
	;;
  stop)
	stop
	;;
  restart)
	restart
        ;;
  condrestart)
	condrestart
	;;
  status)
	status $BASE_DIR/bin/ftmon.pl
	RETVAL=$?
        ;;
  *)
	echo $"Usage: $0 {start|stop|status|condrestart|reload}"
	RETVAL=1
esac

exit $RETVAL
