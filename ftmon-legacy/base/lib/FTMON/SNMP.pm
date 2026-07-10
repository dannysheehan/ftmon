package FTMON::SNMP;
#############################################################################
#                    FTMON (Fast Track Systems Monitor)
#
#   Script: @(#) $RCSfile: SNMP.pm,v $ 
# 
#   DESCRIPTION: 
#
#   @(#) FTMON wrapper for SNMP_Session library.
#
#   $Source: /cvsroot/ftmon/base2/lib/FTMON/SNMP.pm,v $
#
#   $Date: 2003/01/10 13:10:49 $
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
#      Sydney NSW 2122
#      AUSTRALIA
#      dsheehan@ftmon.org
#      http://ftmon.org
#
#############################################################################
require 5.002;

use SNMP_Session;
use BER;
use TraceFuncs;

# Stop errors being directed to standard out.
$SNMP_Session::suppress_warnings = 1;

$DEBUG = 0 if ( ! defined($FTMON::SNMP::DEBUG) );
@FTMON::SNMP::ISA = ("FTMON::Base");

# Hostname/IP Address of alternative host to obtain SNMP values from.
$FT::SNMP::HOST = "localhost";

# Community string to use in snmp requests (default $FT::SNMP::COMMUNITY).
$FT::SNMP::COMMUNITY = "public";

# SNMP port to use ( default $FT::SNMP::PORT).
$FT::SNMP::PORT  = 161;

$_LAST_ATTRIB = $FTMON::Base::_LAST_ATTRIB + 3;
my(
  # Host name to snmp query.
  $HOST,
  # SNMP community string.
  $COMMUNITY,
  # SNMP port.
  $PORT,
  ) = ($FTMON::Base::_LAST_ATTRIB + 1 .. $_LAST_ATTRIB );

# -------------------------------------------------------------
sub new
{
  $DEBUG && TraceFuncs::trace(my $f);

  my $proto  = shift;

  my $host = shift;
  my $community = shift;
  my $port = shift;

  my $class = ref($proto) || $proto;
  my $self = [];

  $self = $class->SUPER::new();

  $host = $FT::SNMP::HOST if ( ! defined $host );
  $host = $FT::HOSTNAME   if ( ! defined $host );
  $host = "localhost"     if ( ! defined $host );

  $community = $FT::SNMP::COMMUNITY if ( ! defined $community );
  $community = "public"             if ( ! defined $community );

  $port = $FT::SNMP::PORT if ( ! defined $port );
  $port = 161             if ( ! defined $port );

  $self->[$HOST] = $host;
  $self->[$COMMUNITY] = $community;
  $self->[$PORT] = $port;

  bless($self, $class);

  return($self);
}

# --------------------------------------------------------------------------
sub host
{
  my $self = shift;
  return($self->[$HOST]);
}

# --------------------------------------------------------------------------
sub community
{
  my $self = shift;
  return($self->[$COMMUNITY]);
}

# --------------------------------------------------------------------------
sub port
{
  my $self = shift;
  return($self->[$PORT]);
}

# --------------------------------------------------------------------------
# Gets SNMP MIB table entries with columns to obtain identified by @l_oid.
# --------------------------------------------------------------------------
sub get_instances
{
  my $self = shift;
  my $oids = shift;

  $DEBUG && TraceFuncs::trace(my $f);

  @FT::VALUES = ();

  my @l_value ;
  my $l_value;

  my @l_oid_addr;
  my $l_oid;
  my @l_e_oid = ();
  foreach ( @$oids )
  {
    $DEBUG && TraceFuncs::debug("    $_");
    @l_oid_addr = split( /\./, $_ ); 
    push( @l_e_oid, [@l_oid_addr] );
  }

  $DEBUG && 
       TraceFuncs::debug("SNMP_Session->open( " .
       $self->host() . ", " . $self->community() . ", " . $self->port() .")");

  my $l_session;
  if ( ! ( $l_session = SNMP_Session->open(
                        $self->host(), $self->community(), $self->port() ) ) )
  {
    if ( $SNMP_Session::errmsg )
    {
      @FT::VALUES = ();
      push(@FT::VALUES, [ 1, $SNMP_Session::errmsg ] );
      return 0;
    }
  }


  $l_session->map_table( 
     [ @l_e_oid ], 
     sub 
     {
       my ($index, @values) = @_;

       grep (defined $_ && ($_ = pretty_print($_) ),
             ( @values ) );

       push(@FT::VALUES, [ 0, "", $index, @values ] );
     } );

  $l_session->close( );

  if ( $SNMP_Session::errmsg )
  {
    @FT::VALUES = ();
    push(@FT::VALUES, [ 1, $SNMP_Session::errmsg ] );
    return 0;
  }

  return 1;
}



# --------------------------------------------------------------------------
# Gets row of SNMP values 
# --------------------------------------------------------------------------
sub get_values
{
  my $self = shift;
  my $oids  = shift;

  $DEBUG && TraceFuncs::trace(my $f);

  my @values = ();
  @FT::VALUES = ();

  my @l_oid_addr;
  my $l_oid;
  my @l_e_oid = ();
  foreach ( @$oids )
  {
    $DEBUG && TraceFuncs::debug($_);

    @l_oid_addr = split( /\./, $_ ); 
    $l_oid = encode_oid( @l_oid_addr );
    push( @l_e_oid, $l_oid );
  }

  my $l_session;
  if ( ! ( $l_session = SNMP_Session->open(
              $self->host(), $self->community(), $self->port() ) ) )
  {
    @FT::VALUES = ();
    push( @FT::VALUES, [ 1, $SNMP_Session::errmsg ] );
    return 0;
  }

  my $l_cols_hash = "";
  my $l_binding;
  my $l_bindings;
  my $l_value;
  if ( $l_session->get_request_response( @l_e_oid ) )
  {
    ( $l_bindings ) = $l_session->decode_get_response($l_session->{pdu_buffer});

    while ( $l_bindings ne '' )
    {
      ( $l_binding, $l_bindings ) = &decode_sequence( $l_bindings );
      ( $l_oid, $l_value) = &decode_by_template ( $l_binding, "%O%@" );
      $l_value = pretty_print($l_value);
      $l_oid = pretty_print($l_oid);
      $DEBUG && TraceFuncs::debug("$l_oid = $l_value");
      push( @values, $l_value );
    }
  }
  else
  {
    @FT::VALUES = ();
    push( @FT::VALUES, [ 1, $SNMP_Session::errmsg ] );
    $l_session->close( );
    return 0;
  }
  
  $l_session->close( );


  if ( $SNMP_Session::errmsg )
  {
    @FT::VALUES = ();
    push( @FT::VALUES, [ 1, $SNMP_Session::errmsg ] );
    return 0;
  }

  push( @FT::VALUES, [ 0, "", @values ] );

  return 1;
}


# --------------------------------------------------------------------------
sub FT::get_values
{
  my $oids = shift;
  my $host = shift;
  my $community = shift;
  my $port = shift;

  my $rrd = FTMON::SNMP->new($host, $community, $port);
  $rrd->get_values($oids);
}

# --------------------------------------------------------------------------
sub FT::get_instances
{
  my $oids = shift;
  my $host = shift;
  my $community = shift;
  my $port = shift;

  my $rrd = FTMON::SNMP->new($host, $community, $port);
  $rrd->get_instances($oids);
}


# --------------------------------------------------------------------------
sub FT::getOctet
{
  local( $l_str, $l_index ) = @_;

  my $l_value = undef;

  my $l_length = length( $l_str );
  my @l_octet  = unpack( "C" x $l_length, $l_str );

  $DEBUG && TraceFuncs::trace(my $f);

  if ( $l_index < 0 )
  {
    $l_value = undef;
    die "ERROR: getOctet( $l_str, $l_index ) - index is < 0" ;
    return( $l_value );

  }

  if ( $l_index >= $l_length )
  {
    $l_value = undef;
    die "ERROR: getOctet( $l_str, $l_index ) - " .
        "index is >= length($l_length) of octet string." ;
    return( $l_value );

  }


  $l_value = $l_octet[$l_index];

  return( $l_value );
}

1;
