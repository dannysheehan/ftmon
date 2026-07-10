package FTMON::Event;
#############################################################################
#                    FTMON (Fast Track Systems Monitor)
#
#   Script: @(#) $RCSfile: Event.pm,v $ 
# 
#   DESCRIPTION: 
#
#   @(#) An event is a condition or "happening" in the environment
#   @(#) that needs attention.
#
#   $Source: /cvsroot/ftmon/base2/lib/FTMON/Event.pm,v $
#
#   $Date: 2003/01/10 13:10:54 $
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
#      PO Box 283
#      Eastwood NSW 2122
#      AUSTRALIA
#      dsheehan@ftmon.org
#      http://ftmon.org
#
#############################################################################
require 5.002;

use TraceFuncs;
use FTMON::Base;

$DEBUG = 0 if ( ! defined($Event::DEBUG) );
@FTMON::Event::ISA = ("FTMON::Base");

$_LAST_ATTRIB = $FTMON::Base::_LAST_ATTRIB + 14;
my(
   # Time that the event was generated.
   $TIME_STAMP,

   # Name of monitor that generated the event.
   $MONITOR_NAME,

   # Unique identifier for the Event.
   $EVENT_ID,

   $HOSTNAME,
   $SEVERITY,
   $MESSAGE,

   # Extra information not contained in the event message.
   # Not implemented.
   $INFORMATION,

   # Extra fields associated with the event.
   # Not implemented.
   $FIELDS,

   $STATUS,
   $REPEAT_COUNT,
   $TTL,
   $TTL_COUNT,
   $SEVERITY_CHANGE,
   ) = ($FTMON::Base::_LAST_ATTRIB + 1 .. $_LAST_ATTRIB );

# ----------------------------------------------------------------------
sub new
{
  $DEBUG && TraceFuncs::trace(my $f);

  my $proto  = shift;

  my $monitor_name = shift;
  my $event_id = shift;
  my $severity = shift;
  my $message = shift;
  my $information = shift;
  my $ttl = shift;

  my $fields = shift;
  my $hostname = shift;

  my $class = ref($proto) || $proto;

  
  if ( ! ( $self = $FTMON::Base::SINGLETON->find_instance($class, $event_id )) )
  {
    $self = $class->SUPER::new($event_id);
    bless($self, $class);
  }
  
  $self->[$TIME_STAMP] = time();

  $self->[$MONITOR_NAME] = $monitor_name;
  $self->[$EVENT_ID] = $event_id;
  $self->[$SEVERITY] = $severity;
  $self->[$MESSAGE] = $message;
  $self->[$TTL] = $self->[$TTL_COUNT] = $ttl;

  $self->[$INFORMATION] = $information;
  $self->[$FIELDS] = $fields;

  $self->[$HOSTNAME] = $hostname;

  $self->[$STATUS] = "OPEN";
  $self->[$REPEAT_COUNT] = 0;
  $self->[$SEVERITY_CHANGE] = 0;


  return($self);
}

# ----------------------------------------------------------------------
sub DESTROY
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self  = shift;

  $self->SUPER::DESTROY();
}

# ----------------------------------------------------------------------
# Returns the time that event was generated.
# ----------------------------------------------------------------------
sub timestamp
{
  my $self = shift;
  
  return($self->[$TIME_STAMP]);
}

# ----------------------------------------------------------------------
sub event_id
{
  my $self = shift;
  if (@_) 
  {
    my $event_id = shift;
    $self->[$EVENT_ID] = $event_id;
  }
  return($self->[$EVENT_ID]);
}

# ----------------------------------------------------------------------
# Get/Set hostname associated with the event.
# NB The hostname may be that of a proxied host.
# ----------------------------------------------------------------------
sub hostname
{
  my $self = shift;
  if (@_) 
  {
    my $hostname = shift;
    $self->[$HOSTNAME] = $hostname;
  }
  return($self->[$HOSTNAME]);
}

# ----------------------------------------------------------------------
# Get/Set the severity of the events
# REVISIT: Checks?
# ----------------------------------------------------------------------
sub severity
{
  my $self = shift;
  if (@_) 
  {
    my $severity = shift;
    $self->[$SEVERITY] = $severity;
  }
  return($self->[$SEVERITY]);
}

# ----------------------------------------------------------------------
# Set/Get severity change.
# TRUE (1) if severity has changed level, otherwise FALSE (0).
# ----------------------------------------------------------------------
sub severity_change
{
  my $self = shift;
  if (@_) 
  {
    my $severity_change = shift;
    $self->[$SEVERITY_CHANGE] = $severity_change;
  }
  return($self->[$SEVERITY_CHANGE]);
}

# ----------------------------------------------------------------------
# Get/Set the event message.
# ----------------------------------------------------------------------
sub message
{
  my $self = shift;
  if (@_) 
  {
    my $message = shift;
    $self->[$MESSAGE] = $message;
  }
  return($self->[$MESSAGE]);
}

# ----------------------------------------------------------------------
# Get/Set the information associated with the Event.
# ----------------------------------------------------------------------
sub information
{
  my $self = shift;
  if (@_) 
  {
    my $information = shift;
    $self->[$INFORMATION] = $information;
  }
  return($self->[$INFORMATION]);
}

# ----------------------------------------------------------------------
# Get/Set event fields associated with the event.
# ----------------------------------------------------------------------
sub fields
{
  my $self = shift;
  if (@_) 
  {
    my $fields = shift;
    $self->[$FIELDS] = $fields;
  }
  return($self->[$FIELDS]);
}

# ----------------------------------------------------------------------
# Get/Set the event status
#   OPEN   - event condition exists
#   CLOSED - event condition no longer exists.
# NOTE
#   If an event is closed it will take TTL_COUNT iterations of the 
#   monitor before the event is actually closed. This feature is used
#   to prevent event being generated continusouly when a monitor value
#   is aliasing around a threhold.
# ----------------------------------------------------------------------
sub status
{
  $DEBUG && TraceFuncs::trace(my $f);

  my $self = shift;
  if (@_) 
  {
    my $status = shift;
    if ( $status eq "CLOSED" )
    {
      if ( $self->[$TTL_COUNT] <= 0 )
     {
        $self->[$STATUS] = $status;
     }
     else
     {
        -- $self->[$TTL_COUNT];
     }
    }
    else
    {
      $self->[$STATUS] = $status;
    }
  }

  $DEBUG && TraceFuncs::debug(
       "ttl=" . $self->[$TTL_COUNT] . 
   "\nstatus=" . $self->[$STATUS]);

  return($self->[$STATUS]);
}


# ----------------------------------------------------------------------
sub repeat_count
{
  $DEBUG && TraceFuncs::trace(my $f);

  my $self = shift;
  if (@_) 
  {
    my $repeat_count = shift;
    $self->[$REPEAT_COUNT] = $repeat_count;
  }
  return($self->[$REPEAT_COUNT]);
}


# ----------------------------------------------------------------------
sub monitor_name
{
  my $self = shift;
  if (@_) 
  {
    my $monitor_name = shift;
    $self->[$MONITOR_NAME] = $monitor_name;
  }
  return($self->[$MONITOR_NAME]);
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

FTMON::Event - 

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
