package FTMON::CalculationManager;
#############################################################################
#                    FTMON (Fast Track Systems Monitor)
#
#   Script: @(#) $RCSfile: CalculationManager.pm,v $ 
# 
#   DESCRIPTION: 
#
#   @(#) The CalculationManager is responsible for activating calculations
#   @(#) and providing persistence for time series type calcuations 
#   @(#) e.g. averaging, slope detection etc.
#   @(#) NB Persistence to disk is not yet implemented. 
#
#   $Source: /cvsroot/ftmon/base2/lib/FTMON/CalculationManager.pm,v $
#
#   $Date: 2003/01/10 13:10:41 $
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

use TraceFuncs;
use FTMON::Calculation;
use FTMON::Base;


$DEBUG = 0 if ( ! defined($FTMON::CalculationManager::DEBUG) );

@FTMON::CalculationManager::ISA = ("FTMON::Base");

my %Calcs = ();

$_LAST_ATTRIB = $FTMON::Base::_LAST_ATTRIB + 5;
my (
    # Unique ID maintained by Calculation Manager and assigned to
    # each calculation that is registered.
    $LAST_CALC_INDEX,

    # Hash of all calculations currently registered.
    $CALCS,

    # The currently active monitor being serviced by Calculation Manager
    # REVISIT: Not a threaded design.
    $ACTIVE_MONITOR,

    # The currently active resource being serviced by Calculation Manager
    # REVISIT: Not a threaded design.
    $ACTIVE_RESOURCE,
   ) = 
       ($FTMON::Base::_LAST_ATTRIB + 1 .. $_LAST_ATTRIB);



# -------------------------------------------------------------------------
sub new
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $proto = shift;

  my $class = ref($proto) || $proto;
  my $self = $class->SUPER::new("CalculationManager");
  
  $self->[$LAST_CALC_INDEX] = 0;
  $self->[$CALCS] =  \%Calcs;
  $self->[$ACTIVE_MONITOR] =  undef;
  $self->[$ACTIVE_RESOURCE] = undef;

  bless($self, $class);

  $self->_load_calcs();

  return($self);
}

$SINGLETON = FTMON::CalculationManager->new();


# ----------------------------------------------------------------------
sub DESTROY
{
  my $self  = shift;
  $self->SUPER::DESTROY();
}


# ----------------------------------------------------------------------
# REVISIT:
# Future functionality to allow calculation values to be saved.
# ----------------------------------------------------------------------
sub feeze_calculations
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;

  # REVISIT:
  my $freeze_str = 
     "\$FTMON::CalculationManager::Calculations->{" . $instance . "} = "
}


# ----------------------------------------------------------------------
# Assign the next Calculation Id.
# ----------------------------------------------------------------------
sub next_calc_id
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;

  if ( ! defined($self->[$ACTIVE_MONITOR]) )
  {
    die "No monitor is currently active";
  }

  if ( ! defined($self->[$ACTIVE_RESOURCE]) )
  {
    die "No resource is currently active";
  }
  
  my $calc_id =  "<" . $self->[$ACTIVE_MONITOR] . ">_" .
                 $self->[$ACTIVE_RESOURCE] .
		   "[" . $self->[$LAST_CALC_INDEX] . "]";
  $self->[$LAST_CALC_INDEX] ++;

  $DEBUG && TraceFuncs::debug("calc_id = $calc_id");

  return $calc_id;
}

# ----------------------------------------------------------------------
# Find a calculation object based on the calculation id.
# ----------------------------------------------------------------------
sub find_calculation
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;
  my $calc_id = shift;
  my $calculation;

  if ( exists($self->[$CALCS]->{$calc_id}) )
  {
    $DEBUG && TraceFuncs::debug("Using pre-existing calculation.");
    $calculation = $self->[$CALCS]->{$calc_id};
  }
  else
  {
    $DEBUG && TraceFuncs::debug("New calculation. $calc_id");
    $calculation = undef;
  }
  return($calculation);
}

# ----------------------------------------------------------------------
# Register a calculation to be managed by CalculationManager
# ----------------------------------------------------------------------
sub register_calculation
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;
  my $calculation = shift;

  my $calc_id = $calculation->calc_id();

  $self->[$CALCS]->{$calc_id} = $calculation;
  $calculation->touched(1);
}


# ----------------------------------------------------------------------
# Unregister a calculation
# ----------------------------------------------------------------------
sub deregister_calculation
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;
  my $calc_id = shift;

  delete $self->[$CALCS]->{$calc_id};
}


# ----------------------------------------------------------------------
# Register new monitor as being active. Collect garbage associated with
# previously active monitor i.e. resource calculations that were not
# updated since the last interval, this indicates that the resource is
# no longer available so there is no need to keep calculations for it.
# FTMON::Monitor will call this subroutine when the run() subroutine is 
# activated.
# ----------------------------------------------------------------------
sub active_monitor
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;
  
  if ( @_ )
  {
    $self->_collect_garbage($self->[$ACTIVE_MONITOR])
                 if ( defined($self->[$ACTIVE_MONITOR]) );

    $self->[$ACTIVE_MONITOR] = shift;
    $self->[$LAST_CALC_INDEX] = 0;
  }

  return($self->[$ACTIVE_MONITOR]);
}

# ----------------------------------------------------------------------
# Register resource as being active.
# FTMON::Monitor->run() will call this subroutine for each resource
# it processes.
# ----------------------------------------------------------------------
sub active_resource
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;
  
  if ( @_ )
  {
    $self->[$ACTIVE_RESOURCE] = shift;
    $self->[$LAST_CALC_INDEX] = 0;
  }

  return($self->[$ACTIVE_RESOURCE]);
}

# ======================================================================
# PRIVATE SUBROUTINES
# ======================================================================

# ----------------------------------------------------------------------
# REVISIT: Not implemented yet.
# ----------------------------------------------------------------------
sub _load_calcs
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;
  my $monitor = shift;
}

# ----------------------------------------------------------------------
# REVISIT: Not implemented yet.
# ----------------------------------------------------------------------
sub _save_calcs
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;
  my $monitor = shift;
}

# ----------------------------------------------------------------------
# To force garbage collection 
# have to do some trickery because everything inherits from FTMON::Base
# ----------------------------------------------------------------------
sub _collect_garbage
{
  $DEBUG && TraceFuncs::trace(my $f);
  my $self = shift;
  my $monitor_name = shift;

  my $id;
  my $calculation;
  my $touched;
  my @deleted;
  while ( ($id, $calculation) = each(%{$self->[$CALCS]}) )
  {
    next if ( $id !~ /^<$monitor_name>/ );
    next if ( ! defined $calculation );

    $touched = $calculation->touched();
    $DEBUG && TraceFuncs::debug("$id touched = $touched" );

    if ( ! $touched )
    {
      $calculation->deleted(1);
      $self->[$CALCS]->{$id} = undef;
      delete $self->[$CALCS]->{$id};
    }
    else
    {
      $calculation->touched(0);
    }
  }
}

1;

__END__;

######################## User Documentation ##########################

## To format the following documentation into a more readable format,
## use one of these programs: perldoc; pod2man; pod2html; pod2text.
## For example, to nicely format this documentation for printing, you
## may use pod2man and groff to convert to postscript:

=head1 NAME

FTMON::CalculationManager - 

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
