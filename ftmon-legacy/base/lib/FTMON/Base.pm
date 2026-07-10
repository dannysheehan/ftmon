#############################################################################
#                    FTMON (Fast Track Systems Monitor)
#
#   Script: @(#) $RCSfile: Base.pm,v $ 
# 
#   DESCRIPTION: 
#
#   @(#) Defines base class used inherited by all FTMON classes.
#   @(#) FTMON is built to monitor itself and this "self monitoring" is
#   @(#) largely implemented in FTMON::Base.
#
#   $Source: /cvsroot/ftmon/base2/lib/FTMON/Base.pm,v $
#
#   $Date: 2003/04/05 03:52:13 $
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
#      PO Box 3228
#      Sydney NSW 1043
#      AUSTRALIA
#      dsheehan@ftmon.org
#      http://ftmon.org
#
#############################################################################
package FTMON::Base;

  $DEBUG = 0 if ( ! defined($FTMON::Base::DEBUG) );

  # Class attributes
  my %ClassList = ();

  my($BASE_OBJECTS,
     $BASE_COUNT) = ( 0 .. 1 );

  $_LAST_ATTRIB = 4;
  my($CHECK,
     $_CLASSLIST,
     $OBJID,
     $DELETED,
     ) = ( 0 .. $_LAST_ATTRIB );

     
  # --------------------------------------------------------------
  sub new
  {
    $DEBUG && TraceFuncs::trace(my $f);
    my $proto = shift;
    my $id    = shift;


    my $class = ref($proto)  || $proto;
    my $self = [];

    $id = $class if ( ! defined $id );

    # private data

    bless($self, $class);

    $self->[$_CLASSLIST] = \%ClassList;
    $self->[$CHECK] = 69;

    $DEBUG && TraceFuncs::debug(
      "duplicate $class instance defined for " . $id )
      if ( exists $self->[$_CLASSLIST]->{$class}->[$BASE_OBJECTS]->{$id} );

    $self->objid($id);

    $self->[$_CLASSLIST]->{$class}->[$BASE_OBJECTS]->{$id} = $self;

    ++$self->[$_CLASSLIST]->{$class}->[$BASE_COUNT];
    $self->[$DELETED] = 0;

    $DEBUG && TraceFuncs::debug( 
	    "class = $class, id = $id, count = " . 
	    $self->[$_CLASSLIST]->{$class}->[$BASE_COUNT] .
            "time = " . $self->[$_CLASSLIST]->{$class}->[$BASE_OBJECTS]->{$id} );
    return($self);
  }

  $SINGLETON = FTMON::Base->new();


  # ----------------------------------------------------------------------
  sub DESTROY
  {
    my $self = shift;
    $DEBUG && TraceFuncs::trace(my $f);

    my $class = ref($self) || $self;
    my $id = $self->objid();

    $DEBUG && TraceFuncs::debug( "class = $class, id = $id" );

    --$self->[$_CLASSLIST]->{$class}->[$BASE_COUNT];

    $DEBUG && TraceFuncs::debug( 
         "count = " . 
         $self->[$_CLASSLIST]->{$class}->[$BASE_COUNT]);

    delete $self->[$_CLASSLIST]->{$class}->[$BASE_OBJECTS]->{$id};
  }

  # ----------------------------------------------------------------------
  sub dump_classes 
  {
    $DEBUG && TraceFuncs::trace(my $f);
    my $self = shift;
    my $dump = shift;

    my $class;
    my $num;
    my $ref;
    my $row = [];
    @{$dump} = ();
    while ( ($class, $ref) = each %{$self->[$_CLASSLIST]} )
    {
      $num = $ref->[$BASE_COUNT];
      $row = [ $class, $num ];
      $DEBUG && TraceFuncs::debug("BUG: " . $class . "|",$num);
      push(@{$dump}, $row);
    }
  }

  # ----------------------------------------------------------------------
  sub find_instance
  {
    $DEBUG && TraceFuncs::trace(my $f);
    my $self = shift;
    my $class = shift;
    my $id = shift;

    if ( defined $self->[$_CLASSLIST]->{$class} &&
         defined $self->[$_CLASSLIST]->{$class}->[$BASE_OBJECTS]->{$id} )
    {
      return $self->[$_CLASSLIST]->{$class}->[$BASE_OBJECTS]->{$id};
    }
    else
    {
      return 0;
    }
  }


  # ----------------------------------------------------------------------
  sub list
  {
    $DEBUG && TraceFuncs::trace(my $f);
    my $self = shift;
    my $class = shift;
    my $list = shift;

    foreach ( values %{$self->[$_CLASSLIST]->{$class}->[$BASE_OBJECTS]} )
    {
      push(@{$list}, $_);
    }
  }



  # ----------------------------------------------------------------------
  sub objid
  {
    $DEBUG && TraceFuncs::trace(my $f);
    my $self = shift;
    
    if (@_) 
    {
      $self->[$OBJID] = shift;
    }

    $DEBUG && TraceFuncs::debug("id = " . $self->[$OBJID]);
    return($self->[$OBJID]);
  }

  # ----------------------------------------------------------------------
  sub deleted
  {
    $DEBUG && TraceFuncs::trace(my $f);
    my $self = shift;
    
    my $id = $self->objid();
    my $class = ref($self) || $self;

    if (@_) 
    {
      $self->[$DELETED] = shift;
      $self->[$_CLASSLIST]->{$class}->[$BASE_OBJECTS]->{$id} = undef;;
    }

    $DEBUG && TraceFuncs::debug("id = " . $self->[$DELETED]);
    return($self->[$DELETED]);
  }

  # ----------------------------------------------------------------------
  sub population
  {
    my $self = shift;
    return($self->[$_NUMOBJS]);
  }

1;
__END__;

######################## User Documentation ##########################

## To format the following documentation into a more readable format,
## use one of these programs: perldoc; pod2man; pod2html; pod2text.
## For example, to nicely format this documentation for printing, you
## may use pod2man and groff to convert to postscript:

=head1 NAME

FTMON::Base - base FTMON class from which all other FTMON classes inherit.

=head1 SYNOPSIS

C<use FTMON::BASE;>

See METHODS section below

=head1 DESCRIPTION

FTMON::Base

=head1 METHODS

=head1 SEE ALSO

=head1 EXAMPLES

=head1 AUTHOR

Danny Sheehan <dsheehan@ftmon.org>

=cut
