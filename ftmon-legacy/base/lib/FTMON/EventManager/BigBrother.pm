package FTMON::EventManager::BigBrother;
#############################################################################
#                    FTMON (Fast Track Systems Monitor)
#
#   Script: @(#) $RCSfile: BigBrother.pm,v $ 
# 
#   DESCRIPTION: 
#
#   @(#) Sends events to a central Big Brother event manager.
#
#   $Source: /cvsroot/ftmon/base2/lib/FTMON/EventManager/BigBrother.pm,v $
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
  $DEBUG = 0 if ( ! defined($FTMON::EventManager::BigBrother::DEBUG) );

  use Socket;
  use FileHandle;

  @FTMON::EventManager::BigBrother::ISA = ("FTMON::EventManager");

  %SEV_DESC =
  (
    "NOEVENT"   => 
    "FTMON base level. Used to represent no event sent. NB An action may still be preformed.",

    "OFFLINE"     => 
    "(Disabled) Notification for this test has been disabled. Used when performing maintenance.",

    "UNAVAILABLE"  => 
    "The associated test has been turned off, or does not apply. A cmmon example is connectivity on disconnected dialup lines",

    "OK"        => 
    "Everything is fine. Have a nice day.",

    "ATTENTION" => 
    "The reporting system has crossed a threshold you should know about",

    "TROUBLE"   => 
    "Bad things are happening",

    "NO_REPORT"   =>
    "No report from this client in the last 30 minutes. They client may have died",
  );

  %SEV::COLOR =
  (
    "NOEVENT"   => "green", 
    "OFFLINE"   => "blue",
    "UNAVAILABLE" => "clear", 
    "OK"        => "green", 
    "ATTENTION" =>  "orange",
    "TROUBLE"   =>  "red",
    "NO_REPORT" => "purple"
  ); 

 @SEV = 
 (
   "NOEVENT",
   "OFFLINE",
   "UNAVAILABLE",
   "OK",
   "OK",
   "ATTENTION",
   "TROUBLE",
   "TROUBLE",
   "TROUBLE",
   "TROUBLE",
   "TROUBLE",
   "NO_REPORT"
  ); 

  # Background Colors
  %SEVERITY_BG_COLOR =
  (
    "UNAVAILABLE"  => "Black",
    "OFFLINE"     => "Blue",
    "NO_REPORT"   => "Purple",
    "TROUBLE"   => "Red",
    "ATTENTION" => "Yellow",
    "OK"        => "#00FF00",
    "NOEVENT"   => "#00FF00",
  );

  # Text Colors
  %SEVERITY_FG_COLOR =
  (
   "UNAVAILABLE"  => "#FFFFFF",
   "OFFLINE"  => "White",
   "NO_REPORT"  => "Black",
   "TROUBLE"  => "Black",
   "ATTENTION"  => "Black",
   "OK"  => "Black",
   "NOEVENT"  => "Black",
  );


  
  $_LAST_ATTRIB = $FTMON::EventManager::_LAST_ATTRIB + 2;
  my($HOST,
     $PORT) =
   ($FTMON::EventManager::_LAST_ATTRIB .. $_LAST_ATTRIB);

  # ----------------------------------------------------------------------
  sub new
  {
    my $proto  = shift;

    my $name = shift;
    my $severity_change = shift;
    my $bb_host = shift;
    my $bb_port = shift;

    $severity_change = 1 if ( ! defined $severity_change );
    my $class = ref($proto) || $proto;

    my $self = $class->SUPER::new($name, $severity_change);

    bless($self, $class);

    $self->set_attribute($HOST, $bb_host);
    $bb_port = 1984 if ( ! defined $bb_port );
    $self->set_attribute($PORT, $bb_port);

    return($self);
  }


  # ----------------------------------------------------------------------
  sub DESTROY
  {
    my $self  = shift;
    $self->SUPER::DESTROY();
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


    # REVISIT
    #my $info;
    #while ( ($key, $value) = each %FT::MONITOR::INFO )
    #{
    #  $info = $info . $key . " | " . $value . "\n";
    #}
    #$info= "";

    my ( $vendor, $product, $monitor ) = split("::", $event->monitor_name());
    $self->report_one(
       $product . "_" . $monitor,
       $event->severity(), 
       $event->event_id() . " : " .
         $event->message() . "\n" . $info,
       $event->timestamp());

    return(1);
  }

  
  # ----------------------------------------------------------------------
  sub bb_host 
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $self = shift;
    return($self->[$HOST]);
  }

  # ----------------------------------------------------------------------
  sub bb_port 
  {
    $DEBUG && TraceFuncs::trace(my $f);

    my $self = shift;
    return($self->[$PORT]);
  }


  sub report_one
  {
    my $self = shift;
    my $item = shift;
    my $status = shift;
    my $comment = shift;
    my $date = shift;


    my $port = $self->bb_port();
    my $ip = $self->bb_host();

    print "$ip || $port \n";

    my $fh = FileHandle->new();
    my $proto_num = (getprotobyname('tcp'))[2];
    my $bind_ip = sockaddr_in(0, INADDR_ANY);

    socket($fh, &PF_INET(), &SOCK_STREAM(), $proto_num) 
       || die "socket(): $!";
    my ($tp, $ti) = sockaddr_in($bind_ip);
    print "Binding to ", $ti, " for ", $bind_ip, "\n";

    bind($fh, $bind_ip) || die "bind(): $!"; 

    $saddr = sockaddr_in($port, inet_aton($ip));
    print "connecting \n";
    connect($fh, $saddr) || die "connect(): $!";

    my $msg = "status " . $FT::HOSTNAME . "." . $item .
              " " . $SEV::COLOR{$status} . " " .
              localtime($date) .
              " $comment";
    $msg .= "\n";
    print "sending $msg\n";
    send($fh, $msg, 0) || die "send(): $!";
    close($fh) || die "close(): $!";
  }

1;

__END__;

######################## User Documentation ##########################

## To format the following documentation into a more readable format,
## use one of these programs: perldoc; pod2man; pod2html; pod2text.
## For example, to nicely format this documentation for printing, you
## may use pod2man and groff to convert to postscript:

=head1 NAME

FTMON::EventManager::BigBrother - 

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
