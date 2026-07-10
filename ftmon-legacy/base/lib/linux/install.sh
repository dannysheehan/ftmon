#############################################################################
#                    FTMON (Fast Track Systems Monitor)
#
#   Script: @(#) $RCSfile: install.sh,v $
#
#   DESCRIPTION:
#
#   @(#) Script used to setup ftmon startup scripts and default configuration.
#   @(#) TBD - Do sanity checks.
#
#   $Source: /cvsroot/ftmon/base2/lib/linux/install.sh,v $
#
#   $Date: 2003/04/27 10:49:06 $
#
#   @(#) $Revision: 1.1 $
###########################################################################

cp sysconfig_ftmon /etc/sysconfig/ftmon
chown root:root /etc/sysconfig/ftmon
chmod 644 /etc/sysconfig/ftmon

cp start_ftmon.sh /etc/init.d/ftmon
chown root:root /etc/init.d/ftmon
chmod 755 /etc/init.d/ftmon
