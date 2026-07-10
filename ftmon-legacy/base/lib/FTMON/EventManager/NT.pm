package FTMON::EventManager::NT;
#############################################################################
#                    FTMON (Fast Track Systems Monitor)
#
#   Script: @(#) $RCSfile: NT.pm,v $ 
# 
#   DESCRIPTION: 
#
#   @(#) Writes events to EventLog, Messenger or plays sounds.
#
#   $Source: /cvsroot/ftmon/base2/lib/FTMON/EventManager/NT.pm,v $
#
#   $Date: 2003/04/20 12:36:18 $
#
#   @(#) $Revision: 1.2 $
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
use Win32::EventLog;
use FTMON::NT;
use Win32::OLE;

# ----------------------------------------------------------------------
  $DEBUG = 0 if ( ! defined($FTMON::EventManager::NT::DEBUG) );


  $ERROR_MSG = "";


  @FTMON::EventManager::NT::ISA = ("FTMON::EventManager");

  @SEV = 
  (
     "NOEVENT",
     "MAINTENANCE",
     "Service Unavailable",
     "Success",
     "Information",
     "Warning",
     "Error",
     "Error",
     "Critical Error",
     "Critical Error",
     "Security Breech",
  ); 

  %SEV_MAP = 
  (
     "NOEVENT" => EVENTLOG_INFORMATION_TYPE,
     "MAINTENANCE" => EVENTLOG_INFORMATION_TYPE,
     "Success" => EVENTLOG_INFORMATION_TYPE,
     "Information" => EVENTLOG_INFORMATION_TYPE,
     "Warning" => EVENTLOG_WARNING_TYPE,
     "Error" => EVENTLOG_ERROR_TYPE,
     "Critical Error" => EVENTLOG_ERROR_TYPE,
     "Security Breech" => EVENTLOG_ERROR_TYPE,
     "Service Unavailable" => EVENTLOG_ERROR_TYPE
  ); 

  @MESSENGER_SEVS_P =
  (
    "Error",
    "Critical Error",
    "Security Breech"
  );

  %MESSENGER_P = 
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
   "Error"           => "BreakingGlass.wav",
   "Critical Error"  => "HighTechAlarm.wav",
   "Security Breech" => "BarkingDog.wav"
  ); 

  
  %SEV_DESC = 
  (
   "NOEVENT" =>
   "No event condition exists.",

   "MAINTENANCE" =>
   "The device application is currently undergoing maintenance",

   "Success" =>
   "Indicates successful event or operation",

   "Information" =>
   "An alert that simply provides information",

   "Warning" =>
   "An alert that might indicate future problems or lower priority issues requiring research",

   "Error" =>
   "An alert that is important and needs attention soon",

   "Critical Error" =>
   "An alert that indicates a serious problem needing attention immediately",

   "Security Breech" =>
   "An alert that indicates a security compromise has occurred. Systems on the network are at risk.",

   "Service Unavailable" =>
   "Events generated for missed agent heartbeats and other events indicating that an application or service is unavailable to its users"
  ); 

  # Background Colors
  %SEVERITY_BG_COLOR = 
  (
   "NOEVENT" => "#FFFFFF",
   "MAINTENANCE" => "#FFFFFF",
   "Success" => "#FFFFFF",
   "Information" => "#FFFFFF",
   "Warning" => "#FFFF00",
   "Error" => "#FF0000",
   "Critical Error" => "#FF0000",
   "Security Breech" => "#FF0000",
   "Service Unavailable" => "#FF0000"
  ); 


  # Text Colors
  %SEVERITY_FG_COLOR =
  (
   "NOEVENT" => "#0000FF",
   "MAINTENANCE" => "#0000FF",
   "Success" => "#0000FF",
   "Information" => "#0000FF",
   "Warning" => "#000000",
   "Error" => "#FFFFFF",
   "Critical Error" => "#FFFFFF",
   "Security Breech" => "#000000",
   "Service Unavailable" => "#000000"
  );


  $SPEECH = 1;

  
  $_LAST_ATTRIB = $FTMON::NT::EventManager::_LAST_ATTRIB + 2;
  my( $SMTP,
      $SPEECH ) =
   ($FTMON::EventManager::NT::_LAST_ATTRIB .. $_LAST_ATTRIB);

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

    $self->[$SPEECH] = 0;
  $SPEECH = 1;
    if ( $SPEECH )
    {
      if ( ! ( $self->[$SPEECH] = Win32::OLE->new('sapi.spVoice') ) )
      {
        die "MS TSS Object: Cannot create: " . Win32::OLE->LastError();
      }
    }

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

    while ( ($host_name, $regex ) = each(%MESSENGER_P) )
    {
      next if ( $event->event_id() !~ /$regex/ );
      next if ( $self->severity_change() && ! $event->severity_change() );
      next if ( ! grep { $_  eq $event->severity() } @MESSENGER_SEVS_P );

    #  $self->popupMessage($host_name, $event);
    }


    #$self->playSound($event) if ( $event->severity_change() );
    if ( $self->[$SPEECH] && $event->severity_change() )
    {
      $self->speakMessage($event);
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
      
      eval
      {
        FTMON::NT::PlaySound($sound_file);
      };
      if ($@ )
      {
        $ERROR_MSG = "playSound(): $@ $!";
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
  
    @msg = ();

    my $severity = $event->severity();
    my $type = $SEV_MAP{$severity};

    my $strings = 
        join(
	  "\0",
          $event->status(),
          $event->event_id(),
          $severity,
          $event->repeat_count(),
          $event->message());

    my $EventLog;
    my %event=(
       'EventID',5,
       'EventType',$type,
       'Category', NULL,
       'Strings', $strings,
       'Data',''
    );
  
    eval
    {
      $EventLog = new Win32::EventLog('FTMON');
      $EventLog->Report(\%event);
    };

    if ($@ )
    {
      $ERROR_MSG = "LogEvent(): $@ $!";
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

    eval
    {
      FTMON::NT::SendMessage($host, $msg);
    };
    if ($@ )
    {
      $ERROR_MSG = "sendMessage(): $@ $!";
    }
  }

  # ----------------------------------------------------------------------
  sub speakMessage
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $self = shift;
    my $event = shift;
  
    my $severity = $event->severity();

    my $msg = 
          $event->status() . " event. Severity " .
          $severity . ". " .
          $event->message();

    eval
    {
	    print $msg, "\n\n\n";
      $self->[$SPEECH]->Speak($msg);
    };
    if ($@ )
    {
      $ERROR_MSG = "speakMessage(): $@ $!";
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

FTMON::EventManager::NT - 

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
