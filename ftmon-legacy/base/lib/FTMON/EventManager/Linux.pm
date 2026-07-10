package FTMON::EventManager::Linux;
#############################################################################
#                    FTMON (Fast Track Systems Monitor)
#
#   Script: @(#) $RCSfile: Linux.pm,v $ 
# 
#   DESCRIPTION: 
#
#   @(#) Writes events to syslog and displays popups or plays sounds.
#
#   $Source: /cvsroot/ftmon/base2/lib/FTMON/EventManager/Linux.pm,v $
#
#   $Date: 2003/01/10 13:11:02 $
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
use FileHandle;

# ----------------------------------------------------------------------
  $DEBUG = 0 if ( ! defined($FTMON::EventManager::Linux::DEBUG) );


  $ERROR_MSG = "";


  @FTMON::EventManager::Linux::ISA = ("FTMON::EventManager");


  @DIALOG_SEVS_P =
  (
    "err",
    "crit",
    "alert",
    "emerg"
  );

  %DIALOG_P = 
  (
    $FT::HOSTNAME => '.*'
  ); 

  %EVENT_LOG_P = 
  (
    $FT::HOSTNAME => '.*'
  );


  %CMD = 
  (
  );


  %SOUND_P = 
  (
   "err"           => "BreakingGlass.wav",
   "emerg"           => "BreakingGlass.wav",
   "alert"  => "HighTechAlarm.wav",
   "crit" => "BarkingDog.wav"
  ); 

  @SEV = 
  (
     "NOEVENT",
     "MAINTENANCE",
     "notice",
     "notice",
     "notice",
     "warning",
     "err",
     "err",
     "crit",
     "alert",
     "emerg",
  ); 
  
  %SEV_DESC = 
  (
   "NOEVENT" =>
   "No event condition exists.",

   "MAINTENANCE" =>
   "The device application is currently undergoing maintenance",

   "notice" =>
   "Normal but significant condition",

   "warning" =>
   "Warning condition",

   "err" =>
   "An alert that is important and needs attention soon",

   "crit" =>
   "An alert that indicates a serious problem needing attention immediately",

   "alert" =>
   "Action must be taken immediately",

   "emerg" =>
   "Events generated for missed agent heartbeats and other events indicating that an application or service is unavailable to its users"
  ); 

  # Background Colors
  %SEVERITY_BG_COLOR = 
  (
   "NOEVENT" => "#FFFFFF",
   "MAINTENANCE" => "#FFFFFF",
   "notice" => "#FFFFFF",
   "warn" => "#FFFF00",
   "err" => "#FF0000",
   "crit" => "#FF0000",
   "alert" => "#FF0000",
   "emerg" => "#FF0000"
  ); 


  # Text Colors
  %SEVERITY_FG_COLOR =
  (
   "NOEVENT" => "#0000FF",
   "MAINTENANCE" => "#0000FF",
   "notice" => "#0000FF",
   "warn" => "#000000",
   "err" => "#FFFFFF",
   "crit" => "#FFFFFF",
   "alert" => "#000000",
   "emerg" => "#000000"
  );

  
  $_LAST_ATTRIB = $FTMON::Linux::EventManager::_LAST_ATTRIB + 1;
  my( $SMTP, ) =
   ($FTMON::EventManager::Linux::_LAST_ATTRIB .. $_LAST_ATTRIB);

  # ----------------------------------------------------------------------
  sub new
  {
    my $proto  = shift;

    my $name = shift;
    my $severity_change = shift;

    $severity_change = 1 if ( ! defined $severity_change );
    my $class = ref($proto) || $proto;

    my $self = $class->SUPER::new($name, $severity_change);

    bless($self, $class);

    $host = "mailhost" if ( ! defined $host );

    return($self);
  }


  # ----------------------------------------------------------------------
  sub DESTROY
  {
    my $self  = shift;
    $self->SUPER::DESTROY();
  }
  
  # ----------------------------------------------------------------------
  sub dump_html
  {
    local($self, *fh) = @_;
    $self->SUPER::dump_html(*fh);
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
  sub send_event 
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $self = shift;
    my $event = shift;

    my $regex;
    my $host_name;
    my $user_name;


    $ERROR_MSG = "";

    while ( ($host_name, $regex ) = each(%EVENT_LOG_P) )
    {
      next if ( $event->event_id() !~ /$regex/ );
      next if ( $self->severity_change() && ! $event->severity_change() );
     
      $self->logEvent($host_name, $event);
    }

    $self->playSound($event);

    while ( ($host_name, $regex ) = each(%DIALOG_P) )
    {
      next if ( $event->event_id() !~ /$regex/ );
      next if ( $self->severity_change() && ! $event->severity_change() );
      next if ( ! grep { $_  eq $event->severity() } @DIALOG_SEVS_P );

      $self->popupMessage($host_name, $event);
    }



    die $ERROR_MSG if ($ERROR_MSG);
  }


  # ----------------------------------------------------------------------
  sub playSound
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $self = shift;
    my $event = shift;

    my $severity = $event->severity();

    if ( exists($SOUND_P{$severity}) && $SOUND_P{$severity} && 
         $event->severity_change() )
    {
      $sound_file = $FT::BASE_DIR . "/sounds/" . $SOUND_P{$severity};
      die "$sound_file does not exist"  if ( ! -f $sound_file );
      
      `/usr/bin/play $sound_file > /dev/null 2>&1`;
      if ($? )
      {
        $ERROR_MSG = "playSound(): $?";
      }
    }
  }
  

  # ----------------------------------------------------------------------
  sub logEvent
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $self = shift;
    my $host = shift;
    my $event = shift;
  
    my $severity = $event->severity();

    my $pri = "daemon." . $severity;
    my $event_id = $event->event_id();
    my $message = 
       $event_id . 
       $event->status() . ":" . 
       $event->message();
    `/usr/bin/logger -p $pri -t FTMON "$message" > /dev/null 2>&1`;

    if ($? )
    {
      $ERROR_MSG = "LogEvent(): $?";
    }
  }

  # ----------------------------------------------------------------------
  sub popupMessage
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $self = shift;
    my $host = shift;
    my $event = shift;
  
    my $severity = $event->severity();

    my $msg = 
          $event->status() . " : " .
          $event->event_id() . " : " .
          $severity . " : " .
          $event->repeat_count() . " : " .
          $event->message();

     system("/bin/sh -c \"/usr/bin/gdialog --msgbox '$msg' 10 300 > /dev/null 2>&1 &\"");
    if ( $? )
    {
      $ERROR_MSG = "sendMessage(): $?";
    }
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

FTMON::EventManager::Linux - 

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
