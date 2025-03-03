# # # Distribution Statement A. Approved for public release. Distribution unlimited.
# # #
# # # Author:
# # # Naval Research Laboratory, Marine Meteorology Division
# # #
# # # This program is free software: you can redistribute it and/or modify it under
# # # the terms of the NRLMMD License included with this program. This program is
# # # distributed WITHOUT ANY WARRANTY; without even the implied warranty of
# # # MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the included license
# # # for more details. If you did not receive the license, for more information see:
# # # https://github.com/U-S-NRL-Marine-Meteorology-Division/

"""Coverage check routine for RGBA center radius coverage checks."""
import logging

import numpy
from geoips.interface_modules.coverage_checks.center_radius import create_radius

LOG = logging.getLogger(__name__)


def center_radius_rgba(
    xarray_obj,
    variable_name,
    area_def=None,
    radius_km=300,
    alt_varname_for_covg=None,
    force_alt_varname=False,
):
    """Coverage check routine for xarray objects with masked projected arrays.

    Only calculates coverage within a "radius_km" radius of center.

    Parameters
    ----------
    xarray_obj : xarray.Dataset
        xarray object containing variable "variable_name"
    variable_name : str
        variable name to check percent unmasked
        radius_km (float) : Radius of center disk to check for coverage

    Returns
    -------
    float
        Percent coverage of variable_name
    """
    varname_for_covg = variable_name
    if (
        variable_name not in xarray_obj.variables.keys()
        and alt_varname_for_covg is not None
    ):
        LOG.info(
            '    UPDATING variable "%s" does not exist, using alternate "%s"',
            variable_name,
            alt_varname_for_covg,
        )
        varname_for_covg = alt_varname_for_covg
    if force_alt_varname and alt_varname_for_covg is not None:
        LOG.info(
            '    UPDATING force_alt_varname set, using alternate "%s" rather than variable "%s"',
            alt_varname_for_covg,
            variable_name,
        )
        varname_for_covg = alt_varname_for_covg

    temp_arr = xarray_obj[varname_for_covg][:, :, 3]

    res_km = (
        min(
            xarray_obj.area_definition.pixel_size_x,
            xarray_obj.area_definition.pixel_size_y,
        )
        / 1000.0
    )
    radius_pixels = 1.0 * radius_km / res_km
    LOG.info(
        "Using %s km radius, %s pixels radius, %s km resolution, area_def %s",
        radius_km,
        radius_pixels,
        res_km,
        area_def,
    )

    dumby_arr = create_radius(
        temp_arr,
        radius_pixels=radius_pixels,
        x_center=temp_arr.shape[0] / 2,
        y_center=temp_arr.shape[1] / 2,
    )

    num_valid_in_radius = numpy.count_nonzero(
        numpy.logical_and(numpy.where(dumby_arr, 1, 0), numpy.where(temp_arr, 1, 0))
    )
    num_total_in_radius = numpy.count_nonzero(dumby_arr)
    return (float(num_valid_in_radius) / num_total_in_radius) * 100.0
