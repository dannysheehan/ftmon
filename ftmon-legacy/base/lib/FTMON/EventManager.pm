package FTMON::EventManager;
#############################################################################
#                    FTMON (Fast Track Systems Monitor)
#
#   Script: @(#) $RCSfile: EventManager.pm,v $ 
# 
#   DESCRIPTION: 
#
#   @(#) Defines base class for use in writing wrappers that enable FTMON
#   @(#) to forward events to other systems management tools.
#   @(#) Uses email as the default mechanism for notifying users of event
#   @(#) conditions.
#
#   $Source: /cvsroot/ftmon/base2/lib/FTMON/EventManager.pm,v $
#
#   $Date: 2003/04/26 16:44:23 $
#
#   @(#) $Revision: 1.5 $
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
#      PO Box 283
#      Eastwood NSW 2122
#      AUSTRALIA
#      dsheehan@ftmon.org
#      http://ftmon.org
#
#############################################################################
require 5.002;

use TraceFuncs;
use Net::SMTP;

use FTMON::Base;
use FTMON::Event;
use FTMON::Monitor;
use FTMON::Helper;
use FTMON::Environment;
use FTMON::Scheduler;

{
package FT;

# Defines how many events to keep historically.
$EVENT_HISTORY_SIZE = 30;

# Set to 1 if you want to get notified of event closure, 0 if you only want
# to know when event conditions are detected.
$CLOSE_EVENTS = 1;

# Defines Time To Live for an event before it is automatically closed.
$EVENT_TTL = 2;

# Name of machine being monitored by this server (proxied).
# $FT::MONITOR::HOST
#


# Defines from field in email. Some paging services require you to set the
# from field for authentication purposes.
$EMAIL_FROM = "ftmon\@localhost";

# Defines severities at which events will be forwarded.
@EMAIL_SEVS = ();

# Defines event groups key is group identifer and the value is a regular
# expression that defines what events can be members of the group.
%GROUP = ();

# Defines what email address (value) to forward events to for given event 
# group (key).
%EMAIL_GROUP = ();

# Defines severities at which events will be spoken (If supported by the OS).
@SPEAK_SEVS = ();

# REVISIT:
%MAINTENANCE = ();
};



$DEBUG = 1 if ( ! defined($FTMON::EventManager::DEBUG) );

$ERROR_MSG = "";

@FTMON::EventManager::ISA = ("FTMON::Base");

#
# When writing event managers you should base mapping on following
# 0 NOEVENT - (This MUST be defined and called NOEVENT)
# 1 (device turned off) MAINTENANCE_MODE,  OFFLINE
# 2 (monitor off) UNKNOWN, INDETERMINATE, NO_REPORT
# 3 (close) Success, CLEAR, HARMLESS, OK
# 4 (info) Information, CLEAR,
# 5 (warning) Warning, WARNING, attention
# 6 Minor
# 7 Major
# 8 Critical
# 9 Fatal
# 10 Security Breech

# Default severity definitions.
@SEV = 
(
   "NOEVENT",
   "MAINTENANCE",
   "INDETERMINATE",
   "CLEAR",
   "CLEAR",
   "WARNING",
   "MINOR",
   "MAJOR",
   "CRITICAL",
   "CRITICAL",
   "CRITICAL"
); 

# Description of each severity.
%SEV_DESC = 
(
   "NOEVENT" =>
   "No event condition exists.",

   "MAINTENANCE" =>
   "The device is currently undergoing maintenance.",

   "INDETERMINATE" =>
   "Indicates that the severity level cannot be determined.",

   "CLEARED" =>
   "Indicates the clearing of one or more previously reported alarms.",

   "WARNING" =>
   "Indicates the detection of a potential or impending service affecting fault before any significant effects have been felt.",

   "MINOR" =>
   "Indicates that a non-service affecting condition has occurred and that corrective action should be taken in order to prevent a more serious fault.",

   "MAJOR" =>
   "Indicates that a service affecting condition has occurred and urgent corrective action is required. Such a severity is used when there is a severe degradation in the capability of the managed entity and its full capability must by restored.",

   "CRITICAL" =>
   "Indicates that a service affecting condition has occurred and immediate corrective action is required. Such a severity is used when the managed entity is used when the managed entity is totally out of service and its capability must be restored."
);

  # Background Colors for each severity.
  %SEVERITY_BG_COLOR =
  (
    CRITICAL      => "#000000",
    MAJOR         => "#8B0000",
    MINOR         => "#FF8000",
    WARNING       => "#FFFF00",
    CLEAR         => "#00FF00",
    INDETERMINATE => "#0000FF",
    NOEVENT       => "#0000FF",
    MAINTENANCE   => "#0000FF"
  );

  # Text Colors
  %SEVERITY_FG_COLOR =
  (
   CRITICAL      => "#FFFFFF",
   MAJOR         => "#000000",
   MINOR         => "#0000FF",
   WARNING       => "#0000FF",
   CLEAR         => "#000000",
   INDETERMINATE => "#FFFFFF",
   NOEVENT       => "#FFFFFF",
   MAINTENANCE   => "#FFFFFF"
  );


  my $HTML_FILE = "events.html";
  my $HTML_HISTORY_FILE = "history.html";

  # Archived Event attributes.
  my ( $AE_TIMESTAMP,
       $AE_HOSTNAME,
       $AE_EVENTID,
       $AE_SEVERITY,
       $AE_MESSAGE,
       $AE_STATUS, ) = ( 0 .. 5 );

  #
  # A T T R I B U T E S
  #
  $_LAST_ATTRIB = $FTMON::Base::_LAST_ATTRIB + 12;
  my(
     # Name of event Manager.
     $NAME,

     # Defines if events generated always or only on severity change.
     $SEV_CHANGE_ONLY,

     # Running counter of total number of events generated by Event Manager.
     $EVENTS_COUNT,

     # Error message associated with sending an event if there was one.
     $ERROR_MSG,

     # Time error message was generated.
     $ERROR_TIME,

     # Running counter of number of errors generated when sending events.
     $ERROR_COUNT,


     # P R I V A T E  A T T R I B U T E S 
     
     # Hash of open events keyed by event identifier. This is events sent
     # on previous iteration of FTMON.
     $_EVENTS,

     # Events to be sent on this iteration of FTMON.
     $_NEW_EVENT_LIST,

     # Events that were successfully sent.
     $_SENT_EVENT_LIST,

     # Events that failed to be sent.  NB ERROR_COUNT counts these.
     $_FAILED_EVENT_LIST,

     # Event history circular buffer. Includes closed events.
     # FT::EVENT_HISTORY_SIZE defines the size of this buffer.
     $_HISTORY_EVENT_LIST,

     # Pointer to circular history buffer.
     $_HISTORY_EVENT_LIST_PTR,

     ) = ($FTMON::Base::_LAST_ATTRIB + 1 .. $_LAST_ATTRIB);
  
  # ----------------------------------------------------------------------
  # post-condition:
  #    - previous event conditions loaded from disk (if monitor scheduled 
  #    externally ).
  #    - 'send_events' method registered with Scheduler to run every cycle.
  #
  sub new
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $proto  = shift;

    my $name = shift;
    my $severity_change = shift;

    # If set to 1 - Events will only be generated on severity change.
    # If set to 0 - Events will be sent every time the event is generated.
    $severity_change = 0 if ( ! defined $severity_change );

    my $class = ref($proto) || $proto;
    
    my $self = $class->SUPER::new($name);
    
 
    $self->[$NAME] = $name;

    # Private data
    $self->[$_EVENTS] = {};
    $self->[$_NEW_EVENT_LIST] = [];
    $self->[$_SENT_EVENT_LIST] = [];
    $self->[$_FAILED_EVENT_LIST] = [];
    $self->[$_HISTORY_EVENT_LIST] = [];
    $self->[$_HISTORY_EVENT_LIST_PTR] = 0;
  
    bless($self, $class);

    $self->set_attribute($SEV_CHANGE_ONLY, $severity_change);

    $self->[$EVENTS_COUNT] = 0;
    $self->[$ERROR_MSG] = 0;
    $self->[$ERROR_TIME] = 0;
    $self->[$ERROR_COUNT] = 0;
  
    return($self);
  }

  # ----------------------------------------------------------------------
  sub DESTROY
  {
    my $self  = shift;
    $self->SUPER::DESTROY();
  }

  # ----------------------------------------------------------------------
  # returns statistics associated with the event manager.
  # ----------------------------------------------------------------------
  sub getStats
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $self  = shift;
    return ($self->[$EVENTS_COUNT],
            $self->[$ERROR_COUNT],
            $self->[$ERROR_MSG],
            $self->[$ERROR_TIME] );
  }

  # ----------------------------------------------------------------------
  # Initialize event manager with local settings.
  # ----------------------------------------------------------------------
  sub init 
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $self  = shift;
    my $sev  = shift;
    my $sev_desc  = shift;
    my $bg_color  = shift;
    my $fg_color  = shift;

    my $i = 0;

    $sev = \@SEV if ( ! defined $sev );

    die "you must define 11 severity levels (FT::SEV) for an Event Manager" 
       if ( @SEV != 11 );
    $sev_desc = \%SEV_DESC if ( ! defined $sev_desc );
    $bg_color = \%SEVERITY_BG_COLOR if ( ! defined $bg_color );
    $fg_color = \%SEVERITY_FG_COLOR if ( ! defined $fg_color );

    @FT::SEV = @{$sev};
    %FT::SEV_DESC = %{$sev_desc};

    %FT::SEVERITY_BG_COLOR = %{$bg_color};
    %FT::SEVERITY_FG_COLOR = %{$fg_color};

    for ( $i=0; $i < @FT::SEV; $i++ )
    {
      $FT::ESEV[$i] = [ 0, $FT::SEV[$i] ];
    }

    $FT::EVENT_MGR = $self;
  }

  # ----------------------------------------------------------------------
  # Allows Event Manager attributes to be set.
  # ----------------------------------------------------------------------
  sub set_attribute 
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $self = shift;
    my $attribute_index = shift;
    my $attribute_value = shift;

    $self->[$attribute_index] = $attribute_value;
  }

  # ----------------------------------------------------------------------
  # Set generation of event only on severity change (on = 1 or off = 0)
  # ----------------------------------------------------------------------
  sub severity_change 
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $self = shift;
    return $self->[$SEV_CHANGE_ONLY];
  }

  # ----------------------------------------------------------------------
  # get/set name of the Event Manager.
  # ----------------------------------------------------------------------
  sub name
  {
    my $self = shift;
    
    if ( @_ )
    {
      $self->[$NAME] = shift;
    }

    return($self->[$NAME]);
  }

  # ----------------------------------------------------------------------
  # Generate unique event key based on hostname and event id.
  # ----------------------------------------------------------------------
  sub event_key
  {
    my $self = shift;

    my $name = shift;
    my $hostname = shift;

    return( $hostname . "::" . $name );
  }



  # ----------------------------------------------------------------------
  # post-condition:
  #    - events associated with specfied monitor that are no longer open 
  #    are closed.
  # ----------------------------------------------------------------------
  sub close_events
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $self = shift;
    my $monitor = shift;

    my $prev_sent_event = undef;
    my $key;

    $ERROR_MSG = "";

    #
    # This will be the previously sent event list from last invocation.
    #
    my @deleted;
    while ( ($key, $prev_sent_event) = each(%{$self->[$_EVENTS]}) )
    {
      next if ( ! defined $prev_sent_event );

      # Close off events for specified monitor only.
      next if ($monitor->name() ne $prev_sent_event->monitor_name() );

      # Close event only if it is not a newly generated event.
      if ( ! @{$self->[$_NEW_EVENT_LIST]} ||
           ! grep { $prev_sent_event == $_}  @{$self->[$_NEW_EVENT_LIST]} )
      {
        $prev_sent_event->status("CLOSED");

       #
       # Check status before sending close event, it have have Time To Live
       # enabled in which case status will not actually change to closed
       # until the TTL has expired.
       #
       my $status = $prev_sent_event->status();
       $DEBUG && TraceFuncs::debug("status =|$status|CLOSED|");
       if ( $status eq "CLOSED" )
       {
          $DEBUG && TraceFuncs::debug("close the event" .
                  "\nseverity=" . $prev_sent_event->severity() .
                  "\nevent_id=" . $prev_sent_event->event_id());

          # Don't perform send the event if the severity is NOEVENT
          # NB Actions will still be performed.
          next if ( ! defined $prev_sent_event->severity()  ||
                    $prev_sent_event->severity() eq "NOEVENT" ||
                    $prev_sent_event->severity() eq "" );

          eval
          {
            # Keep history of events
            $self->archive_event($prev_sent_event);

            if ( $FT::CLOSE_EVENTS )
            {
              $self->send_event($prev_sent_event);
              $self->sendMail($prev_sent_event);
              $self->speakEvent($prev_sent_event);
            }
          };

          $self->[$_EVENTS]->{$key}->deleted(1);
          $self->[$_EVENTS]->{$key} = undef;
          delete $self->[$_EVENTS]->{$key};
        }
      }
    }
  }

  
  # ----------------------------------------------------------------------
  # post-condition:
  #    - new event is opened 
  sub open_event
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $self = shift;
    my $event_id = shift;
    my $monitor = shift;
    my $threshold = shift;

    if ( ! defined $event_id || ! $event_id )
    {
      die "eventid is not defined for " . $threshold->eventid_str();
    }

    my $repeat_count = 0;


    #
    # To cater for proxy monitoring where we are monitoring several
    # other servers from this server, we use the proxied host name (in this 
    # case the MONITOR::HOST) to uniquely identify events.
    #
    my $hostname = (defined($FT::MONITOR::HOST))
                      ? $FT::MONITOR::HOST
                      : $FT::HOSTNAME;

    #
    # Check if this is repeat of pre-existing event condition.
    #
    my $event = $self->find_event($event_id, $hostname);
    if ( defined($event) )
    {
      $DEBUG && TraceFuncs::debug(
             "increment repeat count, " . $event->repeat_count() );
      $repeat_count = $event->repeat_count();
      $repeat_count++;
      $event->repeat_count($repeat_count);
      $event->severity_change(0);

      if ( ! @{$self->[$_NEW_EVENT_LIST]} ||
           ! grep {$event->event_id() eq $_->event_id()}  
              @{$self->[$_NEW_EVENT_LIST]} )
      {
        # REVISIT: Do group events stuff here.
        push(@{$self->[$_NEW_EVENT_LIST]}, $event);
      }
      else 
      {
        $event->message(
          $threshold->message()->current_policy($repeat_count));

        #
        # Only upgrade the severity if it greater than the current severity.
        # This implements severity esculation based on Repeat count.
        # REVISIT: Does this mean severities can not go back down?
        #
        if ( FT::ordered_sev($event->severity()) <
             FT::ordered_sev(
             $threshold->severity()->current_policy($repeat_count)) )
        {
          $event->severity(
            $threshold->severity()->current_policy($repeat_count));
          $event->severity_change(1);
        }
      }


      $DEBUG && TraceFuncs::debug(
           "repeat_count = " . $event->repeat_count() .
           "\nseverity   = " . $event->severity() .
           "\nmessage    = " . $event->message() );

      #
      # Check if there are any actions to perform.
      #
      my $action = $threshold->action();
      if ( defined($action) )
      {
        my ($command_action,
            $command_repeat_count,
            $command_retries) = $action->current_policy(0);

        if ( defined($command_action) )
        {
          if ( $command_retries == -1 ||
               $command_repeat_count + $command_retries > $repeat_count )
          {
            sub timer_sub { return 1 };
            my $job = 
               FTMON::Job->new(
                  "ACTION: $event_id",
                   \&timer_sub,
                   $command_action,
                   $hostname,
                   "Retries = " . 
                   ( $command_retries == -1 ) ? "forever" : $command_retries );
                    
             $FTMON::Scheduler::SINGLETON->now($job);
          }
        }
      }

    }
    else
    {
      $DEBUG && TraceFuncs::debug("- open new event");

      # REVISIT: Needs to be associated/appended with event id
      my $ttl = ( defined($FT::EVENT_TTL) ) ? $FT::EVENT_TTL : 2;


      # REVISIT: 
      my $fields = [];
      my $info = "REVIST:";

      my $severity = $threshold->severity()->current_policy(0);
      my $message  = $threshold->message()->current_policy(0);

      my $action = $threshold->action();
      if ( defined($action) )
      {
        my ($command_action,
            $command_repeat_count,
            $command_retries) = $action->current_policy(0);
        if ( defined($command_action) && $command_action )
        {
          $DEBUG && 
          TraceFuncs::debug("action $command_repeat_count, $command_retries");
          if ( $command_retries == -1 ||
               ( $command_repeat_count + $command_retries > 0 ) )
          {
            sub timer2_sub { return 1 };
            my $job = 
               FTMON::Job->new(
                 "ACTION: $event_id",
                 \&timer2_sub,
                 $command_action,
                 $hostname,
                 ( $command_retries == -1 ) ? "forever" : $command_retries );
                    
            $FTMON::Scheduler::SINGLETON->now($job);
          }
        }
      }

      # No point in creating an event if all we want to do is run command
      # actions.
      return(0) if ( $severity eq "NOEVENT" );


      $event = FTMON::Event->new(
                    $monitor->name(),
                    $event_id,
                    $severity,
                    $message,
                    $info,
                    $ttl,
                    $fields,
                    $hostname);
      $event->severity_change(1);

      #REVIST: Sort out event grouping


      my $key = $self->event_key($event_id, $hostname);

      $self->[$_EVENTS]->{$key} = $event;

      
      # REVISIT: Do group events stuff here.
      push(@{$self->[$_NEW_EVENT_LIST]}, $event)
         if ( ! @{$self->[$_NEW_EVENT_LIST]} ||
              ! grep {$event->event_id() eq $_->event_id()}  
                 @{$self->[$_NEW_EVENT_LIST]} );
    }

    return($event);
  }


  # ----------------------------------------------------------------------
  # Keep circular buffer of previously generated events.
  # ----------------------------------------------------------------------
  sub archive_event
  {
    my $self = shift;
    my $event = shift;

    return if ( $event->repeat_count() > 0 && $event->status() ne "CLOSED" );
    $FT::EVENT_HISTORY_SIZE = 30 if ( ! defined $FT::EVENT_HISTORY_SIZE );

    my @archived_event;

    $archived_event[$AE_TIMESTAMP] = $event->timestamp();
    $archived_event[$AE_EVENTID] = $event->event_id();
    $archived_event[$AE_HOSTNAME] = $event->hostname();
    $archived_event[$AE_SEVERITY] = $event->severity();
    $archived_event[$AE_MESSAGE] = $event->message();
    $archived_event[$AE_STATUS] = $event->status();

    foreach (@{$self->[$_HISTORY_EVENT_LIST]} )
    {
      if ( $archived_event[$AE_EVENTID] eq $_->[$AE_EVENTID] &&
           $archived_event[$AE_TIMESTAMP] == $_->[$AE_TIMESTAMP] )
      {
        $_ = \@archived_event;
        return;
      }
    }

    if ( $self->[$_HISTORY_EVENT_LIST_PTR] > $FT::EVENT_HISTORY_SIZE )
    {
      $self->[$_HISTORY_EVENT_LIST_PTR] = 0;
    }
    $self->[$_HISTORY_EVENT_LIST]->[$self->[$_HISTORY_EVENT_LIST_PTR]] =
        \@archived_event;
    $self->[$_HISTORY_EVENT_LIST_PTR]++;
  }
   


  # ----------------------------------------------------------------------
  sub find_event
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $self = shift;
    my $name = shift;
    my $hostname = shift;

    my $key = $self->event_key($name, $hostname);

    if ( defined($self->[$_EVENTS]->{$key}) )
    {
      $DEBUG && TraceFuncs::debug("found");
      return($self->[$_EVENTS]->{$key});
    }
    else
    {
      $DEBUG && TraceFuncs::debug("not found");
      return(undef);
    }
  }

  # ----------------------------------------------------------------------
  # post-condition:
  #    - newly opened events sent
  #    - non re-opened events closed.
  #    - clear active monitor list
  sub send_events
  {
    $DEBUG && TraceFuncs::trace(my $f);

    $ERROR_MSG = "";

    my $self = shift;

    my $event = undef;
    @{$self->[$_SENT_EVENT_LIST]} = ();
    @{$self->[$_FAILED_EVENT_LIST]} = ();

    foreach $event ( @{$self->[$_NEW_EVENT_LIST]} )
    {
      
      next if ( ! defined $event->severity()  ||
                $event->severity() eq "NOEVENT" ||
                $event->severity() eq "" );

      $self->archive_event($event);
      push(@{$self->[$_SENT_EVENT_LIST]},  $event);

      $DEBUG && TraceFuncs::debug("send event " . $event->event_id() );

      eval
      {
        ++ $self->[$EVENTS_COUNT];
        $self->send_event($event);
        $self->sendMail($event);
        $self->speakEvent($event);
      };

      if ( $@ )
      {
        $ERROR_MSG = $@;
        ++ $self->[$ERROR_COUNT];
        $self->[$ERROR_MSG] = $ERROR_MSG;
        $self->[$ERROR_TIME] = time();
        push(@{$self->[$_FAILED_EVENT_LIST]}, $event);
      }
    }

    # Reset new event list
    $self->[$_NEW_EVENT_LIST] = undef;
    
    my $html_path = $FTMON::Environment::SINGLETON->html_dir() . "/"  .
                    $HTML_FILE;
    open(HTML,  "> $html_path") || 
      die "Could not open $html_path - $!";

    $self->dump_events(HTML);

    close(HTML) ||
      die "Could not write to $html_path - $!";

    $html_path = $FTMON::Environment::SINGLETON->html_dir() . "/"  .
                    $HTML_HISTORY_FILE;
    open(HTML,  "> $html_path") || 
      die "Could not open $html_path - $!";

    $self->dump_history(HTML);
    close(HTML) ||
      die "Could not write to $html_path - $!";

    die $ERROR_MSG if ( $ERROR_MSG );
  }

# ----------------------------------------------------------------------
  BEGIN
  {
    my(@col_names) = 
         ("Severity", "EventID", "Hostname", "Time", "Description", "Count" );
    my(@col_names_history) = 
         ("Time", "Severity", "Status", "EventID", "Hostname", "Description" );
    my(@col_names_event_manager) = 
         ("Level", "Severity", "Description" );

    # post-condition:
    #    - open event sorted and dumped to html file
    sub dump_events
    {
      $DEBUG && TraceFuncs::trace(my $f);

      local($self, *fh) = @_;
      

      FTMON::Helper::http_page_begin(*fh, 
         "events",
     60,
     "<P>The following table shows the events conditions detected by the FTMON monitors. These events are currently been forwarded to the '" .
     $FT::EVENT_MGR->name() .
     "' event manager (class " . ref($FT::EVENT_MGR->name()) . ")." .
     "</P> You can also see a <a href=\"./history.html\">history</a> of the last $FT::EVENT_HISTORY_SIZE events that have occurred.<br>",
         "./");

      FTMON::Helper::http_table_start(*fh, "", \@col_names);

      my $event = undef;
      my @event_details = ();
      my $severity;
      # foreach $event ( @{$self->[$_SENT_EVENT_LIST]} )

      my @sorted_keys = 
        sort 
        { 
          my $sa = $self->[$_EVENTS]->{$a}->severity();
          my $sb = $self->[$_EVENTS]->{$b}->severity();

          defined $sa &&
          defined $sb &&
          FT::ordered_sev($sb) <=> FT::ordered_sev($sa);
        } keys %{$self->[$_EVENTS]};

      foreach $key ( @sorted_keys )
      {
        $event = $self->[$_EVENTS]->{$key};

        next if ( ! defined $event->severity()  ||
                $event->severity() eq "NOEVENT" ||
                $event->severity() eq "" );

        $DEBUG && TraceFuncs::debug($event->event_id());
        my $severity = $event->severity();
        my @severity = ();
        my $fg_color = "white";
        my $bg_color = "black";
        $fg_color = $FT::SEVERITY_FG_COLOR{$severity}
                 if ( exists($FT::SEVERITY_FG_COLOR{$severity}) );
        $bg_color = $FT::SEVERITY_BG_COLOR{$severity}
                 if ( exists($FT::SEVERITY_BG_COLOR{$severity}) );

        if ( $fg_color && $bg_color )
        {
          push(@severity, $bg_color);
          push(@severity, $fg_color);
        }
        push(@severity, $severity);

        my $event_id = $event->event_id();
        my $hostname = $event->hostname();
        my ($vendor, $product, $monitor)= split("/", $event_id);
        # my $html_path = $FTMON::Environment::SINGLETON->html_dir() . "/"  .
        my $html_path = $vendor . "/" .
            $product . "/" .
            $monitor . ".html";
        #if ( -f $html_path  )
        #{
          $event_id = "<a href=\"$html_path\">$event_id</a>";
        #}
        push(@event_details,
          [ @severity,
            $event_id,
            $hostname,
            FT::convert_date(
              '$dd $Mth $yyyy $hh:$mm:$ss', 
              $event->timestamp()),
            $event->message(),
            $event->repeat_count(), 
          ] );
      }

      $fmt_str = 
          '<TR><TD BGCOLOR="%s"><LEFT>' .
          '<FONT SIZE=-1 COLOR="%s">%s</FONT></LEFT></TD>' .
          '<TD><FONT SIZE=-1><LEFT>%s</FONT></LEFT></TD>' .
          '<TD><FONT SIZE=-1><LEFT>%s</FONT></LEFT></TD>' .
          '<TD><FONT SIZE=-1><LEFT>%s</FONT></LEFT></TD>' .
          '<TD><FONT SIZE=-1><LEFT>%s</FONT></LEFT></TD>' .
          '<TD><FONT SIZE=-1><LEFT>%s</FONT></LEFT></TD></TR>';

      FTMON::Helper::print_table(*fh, \@event_details, '-1', $fmt_str);
      FTMON::Helper::http_table_end(*fh);

      FTMON::Helper::http_page_end(*fh);
    }

    # ----------------------------------------------------------------------
    sub dump_history
    {
      $DEBUG && TraceFuncs::trace(my $f);

      local($self, *fh) = @_;
      

      FTMON::Helper::http_page_begin(*fh, 
         "history",
     60,
     "<P>The following table shows a history of the event conditions detected by the FTMON monitors.</P>",
         "./");

      FTMON::Helper::http_table_start(*fh, "", \@col_names_history);

      my $event = undef;
      my @event_details = ();
      my $severity;
      # foreach $event ( @{$self->[$_SENT_EVENT_LIST]} )

      my @sorted_keys;
      my $i;
      for ( $i = $self->[$_HISTORY_EVENT_LIST_PTR]; 
            $i < @{$self->[$_HISTORY_EVENT_LIST]};
        $i++ )
      {
        push(@sorted_keys, $self->[$_HISTORY_EVENT_LIST]->[$i]);
      }

      for ( $i = 0; $i < $self->[$_HISTORY_EVENT_LIST_PTR]; $i++ )
      {
        push(@sorted_keys, $self->[$_HISTORY_EVENT_LIST]->[$i]);
      }


      foreach $event ( @sorted_keys )
      {
        next if ( $event->[$AE_SEVERITY] eq "NOEVENT" );

        my $event_id = $event->[$AE_EVENTID];
        $DEBUG && TraceFuncs::debug($event_id);
    my $severity = $event->[$AE_SEVERITY];
    my @severity = ();
    my $fg_color = "white";
    my $bg_color = "black";
    $fg_color = $FT::SEVERITY_FG_COLOR{$severity}
                 if ( exists($FT::SEVERITY_FG_COLOR{$severity}) );
    $bg_color = $FT::SEVERITY_BG_COLOR{$severity}
                 if ( exists($FT::SEVERITY_BG_COLOR{$severity}) );

        if ( $fg_color && $bg_color )
    {
      push(@severity, $bg_color);
      push(@severity, $fg_color);
    }
    push(@severity, $severity);

    my ($vendor, $product, $monitor)= split("/", $event_id);
        # my $html_path = $FTMON::Environment::SINGLETON->html_dir() . "/"  .
        my $html_path = $vendor . "/" .
            $product . "/" .
            $monitor . ".html";
    #if ( -f $html_path  )
    #{
      $event_id = "<a href=\"$html_path\">$event_id</a>";
    #}
        #("Time", "Severity", "Status", "EventID", "Description" );
    push(@event_details,
      [ FT::convert_date(
          '$dd $Mth $yyyy $hh:$mm:$ss', 
           $event->[$AE_TIMESTAMP]),
        @severity,
        $event->[$AE_STATUS],
        $event_id,
        $event->[$AE_HOSTNAME],
        $event->[$AE_MESSAGE]
      ] );

      }


      $fmt_str = 
          '<TR>' .
          '<TD><FONT SIZE="-1"><LEFT>%s</LEFT></FONT></TD>' .
          '<TD BGCOLOR="%s"><LEFT>' .
          '<FONT COLOR="%s"><FONT SIZE="-1">%s</FONT></COLOR></LEFT></TD>' .
          '<TD><FONT SIZE="-1"><LEFT>%s</LEFT></FONT></TD>' .
          '<TD><FONT SIZE="-1"><LEFT>%s</LEFT></FONT></TD>' .
          '<TD><FONT SIZE="-1"><LEFT>%s</LEFT></FONT></TD>' .
          '<TD><FONT SIZE="-1"><LEFT>%s</LEFT></FONT></TD></TR>';

      FTMON::Helper::print_table(*fh, \@event_details, '+0', $fmt_str);
      FTMON::Helper::http_table_end(*fh);

      FTMON::Helper::http_page_end(*fh);
    }

    sub dump_html
    {
      $DEBUG && TraceFuncs::trace(my $f);

      local($self, *fh) = @_;

      FTMON::Helper::http_page_begin(*fh, 
         "event_manager",
     60,
     "<P>The following table shows the severities supported by the currently active event manager.</P>",
         "./");

      FTMON::Helper::http_table_start(*fh, "", \@col_names_event_manager);

      my $severity = undef;
      my @severity;
      my @severity_details;
      my $fg_color = "white";
      my $bg_color = "black";
      my $description = "unknown";
      my $level = 0;

      foreach $severity ( @FT::SEV )
      {
        $fg_color = "white";
        $bg_color = "black";
        $description = "not defined";

        $fg_color = $FT::SEVERITY_FG_COLOR{$severity}
                 if ( exists($FT::SEVERITY_FG_COLOR{$severity}) );
        $bg_color = $FT::SEVERITY_BG_COLOR{$severity}
                 if ( exists($FT::SEVERITY_BG_COLOR{$severity}) );
        $description = $FT::SEV_DESC{$severity}
                 if ( exists($FT::SEV_DESC{$severity}) );

        push(@severity_details,
          [ 
            $level,
            $bg_color,
            $fg_color,
            $severity,
            $description
          ] );

        $level++;

      }


      $fmt_str = 
          '<TR>' .
          '<TD WIDTH="15%%"><FONT SIZE=-1><LEFT>$FT::ESEV[%s]</FONT></LEFT></TD>' .
          '<TD WIDTH="20%%" BGCOLOR="%s"><LEFT>' .
          '<FONT SIZE=-1 COLOR="%s">%s</COLOR></LEFT></TD>' .
          '<TD WIDTH="65%%"><LEFT><FONT SIZE=-1>%s</FONT></LEFT></TD></TR>';

      FTMON::Helper::print_table(*fh, \@severity_details, '-1', $fmt_str);
      FTMON::Helper::http_table_end(*fh);

      FTMON::Helper::http_page_end(*fh);
    }

  };
 

  # ----------------------------------------------------------------------
  # Send email if eventid matches email group.
  # ----------------------------------------------------------------------
  sub sendMail
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $self = shift;
    my $event = shift;

    my $group_name;
    my $regex;
    my $email_addr;

    my $severity = $event->severity();

    my $subject = 
          $event->status() . " : " .
          $event->event_id() . " : " .
          $severity;

    my $msg = 
         "repeats = " . $event->repeat_count() . " : " .
         $event->message();

    my $smtp;


    while ( ($group_name, $regex ) = each(%FT::GROUP) )
    {
      next if ( $event->event_id() !~ /$regex/ );

      # Only send email on severity changes to avoid pissing off user
      # by generating events every single monitor iteration that a condition 
      # exists.
      next if ( ! $event->severity_change() );

      # Events of specific severities can be configured to be forwarded as
      # email.

      next if ( ! grep { $_  eq FT::ordered_sev($severity) } @FT::EMAIL_SEVS );

      next if ( ! defined $FT::EMAIL_GROUP{$group_name} );

      $email_addr = $FT::EMAIL_GROUP{$group_name};

      eval
      {
        $smtp = Net::SMTP->new($FT::EMAIL_HOST, Timeout => 15, Debug => 1);
        $smtp->mail($FT::EMAIL_FROM);
  
        $smtp->to($email_addr);
        $smtp->data();
        $smtp->datasend("Subject: " . $subject);
        $smtp->datasend("\n\n" . $msg);
        $smtp->dataend();
  
        $smtp->quit();
      };
  
      if ($@ )
      {
        die "sendMail(): $@ $!";
      }
    }
  }

  # ----------------------------------------------------------------------
  # Speak event message if flite exists and depending on severity level
  # ----------------------------------------------------------------------
  sub speakEvent
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $self = shift;
    my $event = shift;

    my $group_name;
    my $regex;

    my $severity = $event->severity();
    my $event_id = $event->event_id();

    die "No severity defined $event_id" if ( ! $severity ) ;
    die "No message defined $event_id" 
            if ( ! defined $event->message || ! $event->message ) ;

    my $msg = 
         $event->status() . " event. " .
         "Severity " . $severity . ". " .
         $event->message() . ".";


    # Only send email on severity changes to avoid pissing off user
    # by generating events every single monitor iteration that a condition 
    # exists.
    return if ( ! $event->severity_change() );
    return if ( $event->repeat_count() > 0 );


    # Events of specific severities can be configured to be forwarded as
    # email.

    return if ( ! grep { $_  eq FT::ordered_sev($severity) } @FT::SPEAK_SEVS );

    my $flite = $FT::BASE_DIR . "/lib/" . $^O . "/flite";
    if ( -f "$flite" )
    {
      open(FLITE, "| $flite") || die "$flite: $!";
      print FLITE "$msg";
      print FLITE "\n";
      close(FLITE) || die "flite: $!";
    }
  }


  # ----------------------------------------------------------------------
  # post-condition:
  #    - 'true' returned if event manager is healthy
  # ----------------------------------------------------------------------
  sub status
  {

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

FTMON::EventManager - 

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

