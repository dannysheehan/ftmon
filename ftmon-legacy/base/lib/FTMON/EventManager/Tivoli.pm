package FTMON::EventManager::Tivoli;
#############################################################################
#                    FTMON (Fast Track Systems Monitor)
#
#   Script: @(#) $RCSfile: Tivoli.pm,v $ 
# 
#   DESCRIPTION: 
#
#   @(#) Writes events to syslog file as they occur to be picked up by
#   @(#) the tivoli logfile adapter.
#
#   $Source: /cvsroot/ftmon/base2/lib/FTMON/EventManager/Tivoli.pm,v $
#
#   $Date: 2003/01/10 13:11:04 $
#
#   @(#) $Revision: 1.1.1.1 $
#    
#
#   Copyright (C) 2001  Danny Sheehan
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 2 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program; if not, write to the Free Software
#   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
#
#      FTMON
#      PO Box 238
#      Eastwood NSW 2122
#      AUSTRALIA
#      dsheehan@ftmon.org
#      http://ftmon.org
#
#############################################################################
require 5.002;

use FTMON::EventManager;

# ----------------------------------------------------------------------
  $DEBUG = 0 if ( ! defined($FTMON::EventManager::Tivoli::DEBUG) );

  $PRIORITY = "daemon.notice";

  @FTMON::EventManager::Tivoli::ISA = ("FTMON::EventManager");

  %SEV_DESC = 
  (
   "NOEVENT" =>
   "No event condition exists.",

   "MAINTENANCE" =>
   "The device is currently undergoing maintenance.",

   "UNKNOWN" =>
   "Indicates that the severity level cannot be determined.",

   "HARMLESS" =>
   "Indicates the clearing of one or more previously reported alarms.",

   "WARNING" =>
   "Indicates the detection of a potential or impending service affecting fault before any significant effects have been felt.",

   "MINOR" =>
   "Indicates that a non-service affecting condition has occurred and that corrective action should be taken in order to prevent a more serious fault.",

   "CRITICAL" =>
   "Indicates that a service affecting condition has occurred and urgent corrective action is required. Such a severity is used when there is a severe degradation in the capability of the managed entity and its full capability must by restored.",

   "FATAL" =>
   "Indicates that a service affecting condition has occurred and immediate corrective action is required. Such a severity is used when the managed entity is used when the managed entity is totally out of service and its capability must be restored."
  );

  @SEV = ();
  $SEV[0] = "NOEVENT";
  $SEV[1] = "MAINTENANCE";
  $SEV[2] = "UNKNOWN";
  $SEV[3] = "HARMLESS";
  $SEV[4] = "HARMLESS";
  $SEV[5] = "WARNING";
  $SEV[6] = "MINOR";
  $SEV[7] = "MINOR";
  $SEV[8] = "CRITICAL";
  $SEV[9] = "FATAL";
  $SEV[10] = "FATAL";

  # Background Colors
  %SEVERITY_BG_COLOR =
  (
    FATAL     => "Black",
    CRITICAL  => "Red",
    MINOR     => "#FF8000",
    WARNING   => "Yellow",
    HARMLESS  => "#00FF00",
    UNKNOWN   => "Blue",
    MAINTENANCE   => "Blue",
    NOEVENT   => "Blue"
  );

  # Text Colors
  %SEVERITY_FG_COLOR =
  (
   FATAL     => "#FFFFFF",
   CRITICAL  => "Black",
   MINOR     => "Blue",
   WARNING   => "Blue",
   HARMLESS  => "Black",
   MAINTENANCE   => "White",
   UNKNOWN   => "White",
   NOEVENT   => "White"
  );


  
  $_LAST_ATTRIB = $FTMON::EventManager::_LAST_ATTRIB + 1;
  my($TEC_HOST, ) =
   ($FTMON::EventManager::_LAST_ATTRIB .. $_LAST_ATTRIB);

  # ----------------------------------------------------------------------
  sub new
  {
    my $proto  = shift;

    my $name = shift;
    my $severity_change = 1;

    my $tec_host = shift;

    $severity_change = 1 if ( ! defined $severity_change );

    my $class = ref($proto) || $proto;

    my $self = $class->SUPER::new($name, $severity_change);

    bless($self, $class);

    $self->set_attribute($TEC_HOST, $tec_host);

    return($self);
  }

   # ----------------------------------------------------------------------
  sub init 
  {
   $DEBUG && TraceFuncs::trace(my $f);

   my $self = shift;
   $self->SUPER::init(
      \@SEV, 
      \%SEV_DESC, 
      \%SEVERITY_BG_COLOR,
      \%SEVERITY_FG_COLOR);
  }



  # ----------------------------------------------------------------------
  sub DESTROY
  {
    my $self  = shift;
    $self->SUPER::DESTROY();
  }
  

  # ----------------------------------------------------------------------
  sub send_event 
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $self = shift;
    my $event = shift;

    #my @cmd =
    #  (
    #    "$FT::BASE_DIR/bin/postemsg",
    #    "-S", $self->tec_host(),
    #    "hostname=" . $FT::HOSTNAME,
	 #    "msg=" . $event->message(),
	 #    "severity=" . $event->severity(),
	 #    "status=" . $event->status(),
	 #    "sub_source=" . $event->event_id(),
	 #    "Unix_checkResource", "POSTEMSG"
    #  );
    #

    my $sub_source = $event->event_id();
    $sub_source =~ s/\/\//\~\//g;
    $sub_source =~ s/\//\~/g;

    my @cmd =
      (
        "/usr/bin/logger",
        "-t", "TIVOLI",
        "-p", $PRIORITY,
        "hostname=" . $FT::HOSTNAME .
	     " severity=" . $event->severity() .
	     " status=" . $event->status() .
	     " sub_source=" . $sub_source .
	     " msg=" . $event->message()
      );

    FT::system_retry(\@cmd);

    return(1);
  }

  
  # ----------------------------------------------------------------------
  sub tec_host 
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $self = shift;
    return($self->[$TEC_HOST]);
  }

1;


__END__;

######################## User Documentation ##########################

## To format the following documentation into a more readable format,
## use one of these programs: perldoc; pod2man; pod2html; pod2text.
## For example, to nicely format this documentation for printing, you
## may use pod2man and groff to convert to postscript:
##   pod2man Net/Telnet.pm | groff -man -Tps > Net::Telnet.ps

=head1 NAME

FTMON::EventManager::Tivoli - 

=head1 SYNOPSIS

See METHODS section below

=head1 DESCRIPTION

FTMON::Base

=head1 METHODS

=head1 SEE ALSO

=head1 EXAMPLES

=head1 AUTHOR

Danny Sheehan <dsheehan@ftmon.org>

=cut
