package FTMON::NT;
#############################################################################
#                    FTMON (Fast Track Systems Monitor)
#
#   Script: @(#) $RCSfile: NT.pm,v $ 
# 
#   DESCRIPTION: 
#
#   @(#) This module allows access to Win32 objects for services, process, 
#   @(#) applications, sesssions, logged in users and perfmon counters etc.
#
#   $Source: /cvsroot/ftmon/base2/lib/FTMON/NT/NT.pm,v $
#
#   $Date: 2003/01/10 13:11:12 $
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
#      PO Box 3228
#      Sydney NSW 1043
#      AUSTRALIA
#      dsheehan@ftmon.org
#      http://ftmon.org
#
#############################################################################
use 5.006;
use strict;
use warnings;

require Exporter;
require DynaLoader;

our @ISA = qw(Exporter DynaLoader);

# Items to export into callers namespace by default. Note: do not export
# names by default without a very good reason. Use EXPORT_OK instead.
# Do not simply export all your public functions/methods/constants.

# This allows declaration	use FTMON::NT ':all';
# If you do not need this, moving things directly into @EXPORT or @EXPORT_OK
# will save memory.
our %EXPORT_TAGS = ( 'all' => [ qw(
	
) ] );

our @EXPORT_OK = ( @{ $EXPORT_TAGS{'all'} } );

our @EXPORT = qw(
	
);
our $VERSION = '0.01';

bootstrap FTMON::NT $VERSION;

# Preloaded methods go here.

1;
__END__

=head1 NAME

NT - Perl extension for NT systems management functions.

=head1 SYNOPSIS

  use NT;
  

=head1 DESCRIPTION

This module allows access to Win32 objects for services, process, applications,
sesssions, logged in users and perfmon counters etc.

=head2 EXPORT

None by default.


=head1 AUTHOR

D.W.Sheehan, E<lt>dsheehan@ftmon.com<gt>

=head1 SEE ALSO

L<perl>.

=cut
