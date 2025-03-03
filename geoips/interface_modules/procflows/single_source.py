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

"""Processing workflow for single data source processing."""
import os
import logging
from datetime import timedelta
import inspect
from importlib import import_module

import xarray

# Internal utilities
from geoips.utils.memusg import print_mem_usage
from geoips.dev.utils import output_process_times
from geoips.xarray_utils.data import sector_xarrays
from geoips.filenames.base_paths import PATHS as gpaths
from geoips.geoips_utils import copy_standard_metadata

# Old interfaces (YAML, not updated to classes yet!)
from geoips.dev.product import (
    get_required_variables,
    get_requested_datasets_for_variables,
    get_product_type,
    get_product_display_name,
    get_covg_from_product,
    get_covg_args_from_product,
    get_cmap_name,
    get_cmap_args,
    get_interp_name,
    get_interp_args,
    get_alg_name,
    get_alg_args
)
from geoips.dev.output_config import (
    get_filename_formats,
    get_filename_format_kwargs,
    get_output_format,
    get_output_format_kwargs,
    get_metadata_filename_format,
    get_metadata_filename_format_kwargs,
    get_metadata_output_format,
    get_metadata_output_format_kwargs,
    get_minimum_coverage,
)

# New class-based interfaces
from geoips.interfaces import colormaps
from geoips.interfaces import output_formats
from geoips.interfaces import filename_formats
from geoips.interfaces import interpolators
from geoips.interfaces import algorithms

# These output families require an input filename list, AND require the returned list of products to
# match what was passed in
OUTPUT_FAMILIES_WITH_OUTFNAMES_ARG = [
    "xrdict_varlist_outfnames_to_outlist",
    "xrdict_area_product_outfnames_to_outlist",
]
# These output families do NOT take in a list of filenames, and an arbitrary list of output products
# can be returned - there is no expected output file list
OUTPUT_FAMILIES_WITH_NO_OUTFNAMES_ARG = [
    "xrdict_area_product_to_outlist",
]

FILENAME_FORMATS_WITHOUT_COVG = [
    "xarray_metadata_to_filename",
    "xarray_area_product_to_filename",
]

FILENAME_FORMATS_FOR_XARRAY_DICT_TO_OUTPUT_FORMAT = [
    "xarray_metadata_to_filename",
    "xarray_area_product_to_filename",
]

PRODUCT_FAMILIES_FOR_XARRAY_DICT_TO_OUTPUT_FORMAT = [
    "sectored_xarray_dict_to_output_format",
    "unsectored_xarray_dict_to_output_format",
    "unsectored_xarray_dict_area_to_output_format",
]


PMW_NUM_PIXELS_X = 1400
PMW_NUM_PIXELS_Y = 1400
PMW_PIXEL_SIZE_X = 1000
PMW_PIXEL_SIZE_Y = 1000

LOG = logging.getLogger(__name__)

procflow_type = "standard"


def output_all_metadata(
    output_dict, output_fnames, metadata_fnames, xarray_obj, area_def=None
):
    """Output all metadata."""
    final_outputs = output_fnames.copy()
    metadata_output_format = get_metadata_output_format(output_dict)
    metadata_output_format_kwargs = get_metadata_output_format_kwargs(output_dict)
    for metadata_fname, output_fname in zip(metadata_fnames, output_fnames):
        if metadata_fname is not None:
            # Optional arguments for standard metadata formats (like "output_dict")
            metadata_output_format_kwargs["metadata_fname_dict"] = metadata_fnames[
                metadata_fname
            ]
            metadata_output_format_kwargs["output_fname_dict"] = output_fnames[
                output_fname
            ]
            metadata_output_format_kwargs["output_dict"] = output_dict
            output_plugin = output_formats.get_plugin(metadata_output_format)
            output_kwargs = remove_unsupported_kwargs(
                output_plugin, metadata_output_format_kwargs
            )
            if output_plugin.family == "standard_metadata":
                curr_outputs = output_plugin(
                    area_def,
                    xarray_obj=xarray_obj,
                    metadata_yaml_filename=metadata_fname,
                    product_filename=output_fname,
                    **output_kwargs,
                )
                if curr_outputs != [metadata_fname]:
                    raise ValueError("Did not produce expected products")

                for curr_output in curr_outputs:
                    final_outputs[curr_output] = metadata_fnames[curr_output]

    return final_outputs


def get_output_filenames(
    fname_formats,
    output_dict,
    product_name,
    xarray_obj=None,
    area_def=None,
    supported_filenamer_types=None,
):
    """Get output filenames."""
    output_fnames = {}
    metadata_fnames = {}
    for filename_format in fname_formats:
        filename_format_kwargs = get_filename_format_kwargs(
            filename_format, output_dict
        )
        metadata_filename_format = get_metadata_filename_format(
            filename_format, output_dict
        )
        metadata_filename_format_kwargs = get_metadata_filename_format_kwargs(
            metadata_filename_format, output_dict
        )

        output_fname = get_filename(
            filename_format,
            product_name,
            xarray_obj,
            area_def,
            output_dict=output_dict,
            supported_filenamer_types=supported_filenamer_types,
            filename_format_kwargs=filename_format_kwargs,
        )

        # If we weren't able to get a valid output filename, do not proceed.
        if output_fname is None:
            continue

        output_fnames[output_fname] = {
            "filename_format": filename_format,
            "filename_format_kwargs": filename_format_kwargs,
            "product_name": product_name,
        }

        metadata_fname = None
        if metadata_filename_format:
            fname_fmt_plugin = filename_formats.get_plugin(metadata_filename_format)
            if fname_fmt_plugin.family == "standard_metadata":
                metadata_filename_format_kwargs = remove_unsupported_kwargs(
                    fname_fmt_plugin, metadata_filename_format_kwargs
                )
                metadata_fname = fname_fmt_plugin(
                    area_def,
                    xarray_obj,
                    output_fname,
                    **metadata_filename_format_kwargs,
                )
        metadata_fnames[metadata_fname] = {
            "filename_format": filename_format,
            "filename_format_kwargs": filename_format_kwargs,
            "metadata_filename_format": metadata_filename_format,
            "metadata_filename_format_kwargs": metadata_filename_format_kwargs,
            "product_name": product_name,
        }
    return output_fnames, metadata_fnames


def remove_unsupported_kwargs(module, requested_kwargs):
    """Remove unsupported keyword arguments."""
    unsupported = list(
        set(requested_kwargs.keys()).difference(
            set(inspect.signature(module).parameters.keys())
        )
    )
    for key in unsupported:
        LOG.warning("REMOVING UNSUPPORTED %s key %s", module, key)
        requested_kwargs.pop(key)
    return requested_kwargs


def add_filename_extra_field(xarray_obj, field_name, field_value):
    """Add filename extra field."""
    if "filename_extra_fields" not in xarray_obj.attrs:
        xarray_obj.attrs["filename_extra_fields"] = {}
    xarray_obj.attrs["filename_extra_fields"][field_name] = field_value
    return xarray_obj


def combine_filename_extra_fields(source_xarray, dest_xarray):
    """Combine filename extra fields."""
    if "filename_extra_fields" in source_xarray.attrs:
        for field in source_xarray.filename_extra_fields:
            if "filename_extra_fields" not in dest_xarray.attrs:
                dest_xarray.attrs["filename_extra_fields"] = {}
            dest_xarray.attrs["filename_extra_fields"][
                field
            ] = source_xarray.filename_extra_fields[field]
    return dest_xarray


def process_sectored_data_output(
    xobjs, variables, product_name, output_dict, area_def=None
):
    """Process sectored data output."""
    output_products = []
    if (
        get_product_type(product_name, xobjs["METADATA"].source_name)
        == "sectored_xarray_dict_to_output_format"
    ):
        # xdict = {}
        # dsnum = 0
        # for sect_xarray in xobjs:
        #     xdict[f'DS{dsnum}'] = sect_xarray
        #     dsnum += 1
        # xdict['METADATA'] = xobjs[0][[]]
        output_products += process_xarray_dict_to_output_format(
            xobjs, variables, product_name, output_dict, area_def=area_def
        )
    return output_products


def process_xarray_dict_to_output_format(
    xobjs, variables, product_name, output_dict, area_def=None
):
    """Process xarray dict to output format."""
    output_format = get_output_format(output_dict)

    output_format_kwargs = get_output_format_kwargs(output_dict)

    supported_product_types = PRODUCT_FAMILIES_FOR_XARRAY_DICT_TO_OUTPUT_FORMAT

    product_type = get_product_type(product_name, xobjs["METADATA"].source_name)
    if product_type not in supported_product_types:
        raise TypeError(
            f"UNSUPPORTED product_type {product_type} "
            f'for product {product_name} source {xobjs["METADATA"].source_name} \n'
            f"      product_type must be one of {supported_product_types}"
        )

    # These are all the supported oututter familes
    supported_output_plugin_types = (
        OUTPUT_FAMILIES_WITH_OUTFNAMES_ARG + OUTPUT_FAMILIES_WITH_NO_OUTFNAMES_ARG
    )

    output_plugin = output_formats.get_plugin(output_format)

    # Only get output filenames if needed
    if output_plugin.family in OUTPUT_FAMILIES_WITH_OUTFNAMES_ARG:
        supported_filenamer_types = FILENAME_FORMATS_FOR_XARRAY_DICT_TO_OUTPUT_FORMAT
        fname_formats = get_filename_formats(output_dict)
        output_fnames, metadata_fnames = get_output_filenames(
            fname_formats,
            output_dict,
            product_name,
            xarray_obj=xobjs["METADATA"],
            area_def=area_def,
            supported_filenamer_types=supported_filenamer_types,
        )

    if "output_dict" not in output_format_kwargs:
        output_format_kwargs["output_dict"] = output_dict
    output_format_kwargs = remove_unsupported_kwargs(output_plugin, output_format_kwargs)

    if output_plugin.family == "xrdict_varlist_outfnames_to_outlist":
        curr_products = output_plugin(
            xobjs, variables, list(output_fnames.keys()), **output_format_kwargs
        )
        # If we pass it a list of filenames, assume we create exactly those filenames
        if curr_products != list(output_fnames.keys()):
            raise (ValueError("Did not produce expected products"))

    elif output_plugin.family == "xrdict_area_product_outfnames_to_outlist":
        curr_products = output_plugin(
            xobjs,
            area_def,
            product_name,
            list(output_fnames.keys()),
            **output_format_kwargs,
        )
        # If we pass it a list of filenames, assume we create exactly those filenames
        if curr_products != list(output_fnames.keys()):
            raise (ValueError("Did not produce expected products"))

    elif output_plugin.family == "xrdict_area_product_to_outlist":
        curr_products = output_plugin(xobjs, area_def, product_name, **output_format_kwargs)
        # No input filename list, no check that output filename list matches
        LOG.info("Not checking output file list for output family %s", output_plugin.family)

    else:
        raise TypeError(
            f'UNSUPPORTED output_format "{output_format}" '
            f"for product_family {product_type}\n"
            f'      output_plugin_family: "{output_plugin.family}"\n'
            f"      output_plugin_type must be one of {supported_output_plugin_types}"
        )

    # We only pre-generated metadata filenames if we also pre-generated output
    # product filenames
    if output_plugin.family in OUTPUT_FAMILIES_WITH_OUTFNAMES_ARG:
        final_products = output_all_metadata(
            output_dict,
            output_fnames,
            metadata_fnames,
            xobjs["METADATA"],
            area_def=area_def,
        )

    return final_products


def print_area_def(area_def, print_str):
    """Print area def."""
    LOG.info(
        f"\n\n************************************************************************************"
        f"\n***{print_str}\n{area_def}"
    )
    for key, value in area_def.sector_info.items():
        print(f"{key}: {value}")
    print(
        f"************************************************************************************"
    )


def pad_area_definition(
    area_def, source_name=None, force_pad=False, x_scale_factor=1.5, y_scale_factor=1.5
):
    """Pad area definition."""
    from geoips.sector_utils.utils import is_sector_type

    # Always pad TC sectors, and if "force_pad=True" is passed into the function
    if is_sector_type(area_def, "tc") or force_pad:
        LOG.info("Trying area_def %s, %s", area_def.name, area_def.sector_info)
        # Get an extra 50% size for TCs so we can handle recentering and not have missing data.
        # --larger area for possibly moved center for vis/ir backgrounds
        # Default to 1.5x padding
        num_lines = int(area_def.y_size * y_scale_factor)
        num_samples = int(area_def.x_size * x_scale_factor)
        # Need full swath width for AMSU-B and MHS. Need a better solution for this.
        if source_name is not None and source_name in ["amsu-b", "mhs"]:
            num_lines = int(area_def.y_size * 1)
            num_samples = int(area_def.x_size * 5)

        # TC sectors have center lat and center lon defined within the sector_info
        # For other sectors, use lat_0 and lon_0 from proj_dict
        # Do not use proj_dict for TC sectors, because we want the center of the
        # storm, not current center of image.
        if is_sector_type(area_def, "tc"):
            clat = area_def.sector_info["clat"]
            clon = area_def.sector_info["clon"]
        else:
            clat = area_def.proj_dict["lat_0"]
            clon = area_def.proj_dict["lon_0"]

        from geoips.interface_modules.area_def_generators.clat_clon_resolution_shape import (
            clat_clon_resolution_shape,
        )

        pad_area_def = clat_clon_resolution_shape(
            area_id=area_def.area_id,
            long_description=area_def.description,
            clat=clat,
            clon=clon,
            projection="eqc",
            num_lines=num_lines,
            num_samples=num_samples,
            pixel_width=area_def.pixel_size_x,
            pixel_height=area_def.pixel_size_y,
        )
        from geoips.sector_utils.utils import copy_sector_info

        pad_area_def = copy_sector_info(area_def, pad_area_def)
    else:
        pad_area_def = area_def
    return pad_area_def


def get_filename(
    filename_format,
    product_name=None,
    alg_xarray=None,
    area_def=None,
    supported_filenamer_types=None,
    output_dict=None,
    filename_format_kwargs=None,
):
    """Get filename."""
    filename_fmt_plugin = filename_formats.get_plugin(filename_format)
    if (
        supported_filenamer_types is not None
        and filename_fmt_plugin.family not in supported_filenamer_types
    ):
        raise TypeError(
            f'UNSUPPORTED filename_format "{filename_format}" '
            f'      filenamer_type: "{filename_fmt_plugin.family}"\n'
            f"      filenamer_type must be one of {supported_filenamer_types}"
        )

    # They all use covg except those in list
    if (filename_fmt_plugin.family not in FILENAME_FORMATS_WITHOUT_COVG):
        covg_func = get_covg_from_product(
            product_name,
            alg_xarray.source_name,
            output_dict=output_dict,
            covg_func_field_name="fname_covg_func",
        )
        covg_args = get_covg_args_from_product(
            product_name,
            alg_xarray.source_name,
            output_dict=output_dict,
            covg_args_field_name="fname_covg_args",
        )
        covg = covg_func(alg_xarray, product_name, area_def, **covg_args)

    curr_kwargs = remove_unsupported_kwargs(filename_fmt_plugin, filename_format_kwargs)
    if filename_fmt_plugin.family == "data":
        fname = filename_fmt_plugin(
            area_def,
            alg_xarray,
            [product_name, "latitude", "longitude"],
            covg,
            **curr_kwargs,
        )
    elif filename_fmt_plugin.family == "xarray_metadata_to_filename":
        fname = filename_fmt_plugin(alg_xarray, **curr_kwargs)
    elif (
        filename_fmt_plugin.family ==
        "xarray_area_product_to_filename"
    ):
        fname = filename_fmt_plugin(alg_xarray, area_def, product_name, **curr_kwargs)
    else:
        fname = filename_fmt_plugin(area_def, alg_xarray, product_name, covg, **curr_kwargs)
    return fname


def plot_data(
    output_dict,
    alg_xarray,
    area_def,
    product_name,
    output_kwargs,
    fused_xarray_dict=None,
    no_output=False,
):
    """Plot data.

    alg_xarray used for filename formats, etc.
    If included, fused_xarray_dict used for output format call
    """
    # If keyword argument is allowed for output function, include it
    output_kwargs["output_dict"] = output_dict
    output_format = get_output_format(output_dict)
    output_plugin = output_formats.get_plugin(output_format)

    if no_output or output_plugin.family in OUTPUT_FAMILIES_WITH_NO_OUTFNAMES_ARG:
        output_fnames = {}
        metadata_fnames = {}
    else:
        fname_formats = get_filename_formats(output_dict)
        output_fnames, metadata_fnames = get_output_filenames(
            fname_formats, output_dict, product_name, alg_xarray, area_def
        )

    if output_plugin.family == "xarray_data":
        output_products = output_plugin(
            xarray_obj=alg_xarray,
            product_names=[product_name, "latitude", "longitude"],
            output_fnames=list(output_fnames.keys()),
        )
        if output_products != list(output_fnames.keys()):
            raise ValueError("Did not produce expected products")
    else:
        cmap_plugin_name = get_cmap_name(product_name, alg_xarray.source_name)
        mpl_colors_info = None
        if cmap_plugin_name is not None:
            cmap_plugin = colormaps.get_plugin(cmap_plugin_name)
            cmap_args = get_cmap_args(product_name, alg_xarray.source_name)
            mpl_colors_info = cmap_plugin(**cmap_args)

        output_plugin = output_formats.get_plugin(output_format)
        output_kwargs = remove_unsupported_kwargs(output_plugin, output_kwargs)
        if output_plugin.family == "image":
            # This returns None if not specified
            output_products = output_plugin(
                area_def,
                xarray_obj=alg_xarray,
                product_name=product_name,
                output_fnames=list(output_fnames.keys()),
                product_name_title=get_product_display_name(
                    product_name, alg_xarray.source_name
                ),
                mpl_colors_info=mpl_colors_info,
                **output_kwargs,
            )
            if output_products != list(output_fnames.keys()):
                raise ValueError("Did not produce expected products")
        elif output_plugin.family == "unprojected":
            # This returns None if not specified
            output_products = output_plugin(
                xarray_obj=alg_xarray,
                product_name=product_name,
                output_fnames=list(output_fnames.keys()),
                product_name_title=get_product_display_name(
                    product_name, alg_xarray.source_name
                ),
                mpl_colors_info=mpl_colors_info,
                **output_kwargs,
            )
            if output_products != list(output_fnames.keys()):
                raise ValueError("Did not produce expected products")
        elif output_plugin.family == "image_overlay":
            # This can include background information, gridlines/boundaries plotting
            # information, etc
            output_products = output_plugin(
                area_def,
                xarray_obj=alg_xarray,
                product_name=product_name,
                output_fnames=list(output_fnames.keys()),
                product_name_title=get_product_display_name(
                    product_name, alg_xarray.source_name
                ),
                mpl_colors_info=mpl_colors_info,
                **output_kwargs,
            )
            if output_products != list(output_fnames.keys()):
                raise ValueError("Did not produce expected products")
        elif output_plugin.family == "xrdict_area_product_outfnames_to_outlist":
            # For xarray_dict type, pass the full fused_xarray_dict.
            output_kwargs["product_name_title"] = get_product_display_name(
                product_name, alg_xarray.source_name
            )
            output_kwargs["mpl_colors_info"] = mpl_colors_info
            output_kwargs = remove_unsupported_kwargs(output_plugin, output_kwargs)
            output_products = output_plugin(
                xarray_dict=fused_xarray_dict,
                area_def=area_def,
                product_name=product_name,
                output_fnames=list(output_fnames.keys()),
                **output_kwargs,
            )
            if output_products != list(output_fnames.keys()):
                raise ValueError("Did not produce expected products")
        elif output_plugin.family == "xrdict_area_product_to_outlist":
            # For xarray_dict type, pass the full fused_xarray_dict.
            output_kwargs["product_name_title"] = get_product_display_name(
                product_name, alg_xarray.source_name
            )
            output_kwargs["mpl_colors_info"] = mpl_colors_info
            output_kwargs = remove_unsupported_kwargs(output_plugin, output_kwargs)
            output_products = output_plugin(
                xarray_dict=fused_xarray_dict,
                area_def=area_def,
                product_name=product_name,
                **output_kwargs,
            )
            # No input filename list, no check that output filename list matches
            LOG.info(
                "Not checking output file list for output family %s",
                output_plugin.family
            )
        else:
            raise ValueError(
                f"Unsupported output family {output_plugin.family} "
                f"for output format {output_format}"
            )

    all_final_products = output_all_metadata(
        output_dict, output_fnames, metadata_fnames, alg_xarray, area_def
    )

    return all_final_products


def get_area_defs_from_command_line_args(
    command_line_args, xobjs, variables=None, filter_time=True
):
    """Get area def from command line args."""
    from geoips.sector_utils.utils import (
        get_static_area_defs_for_xarray,
        get_tc_area_defs_for_xarray,
    )
    from geoips.sector_utils.utils import get_trackfile_area_defs
    from geoips.sector_utils.utils import filter_area_defs_actual_time

    sectorfiles = None
    sector_list = None
    tcdb_sector_list = None
    tcdb = None
    trackfile_sector_list = None
    trackfiles = None
    trackfile_parser = None
    tc_template_yaml = None
    self_register_dataset = None
    self_register_source = None
    area_defs = []

    # If we are requesting an area definition that is tied directly to the reader METADATA, identify it here.
    # This is useful for datasets that are pre-registered to a specific region
    # (like TCs, etc)
    if (
        "reader_defined_area_def" in command_line_args
        and command_line_args["reader_defined_area_def"]
    ):
        area_def = xobjs["METADATA"].attrs["area_definition"]

        # Provide standard area_def information that GeoIPS expects
        if not hasattr(area_def, "sector_type"):
            area_def.attrs["sector_type"] = "reader_defined"

        if not hasattr(area_def, "name"):
            setattr(area_def, "name", area_def.sector_type)

        if not hasattr(area_def, "area_id"):
            setattr(area_def, "area_id", area_def.name)

        if not hasattr(area_def, "description"):
            setattr(area_def, "description", area_def.name)

        area_defs += [area_def]
    if "sectorfiles" in command_line_args:
        sectorfiles = command_line_args["sectorfiles"]
    if "sector_list" in command_line_args:
        sector_list = command_line_args["sector_list"]
    if "tcdb_sector_list" in command_line_args:
        tcdb_sector_list = command_line_args["tcdb_sector_list"]
    if "tcdb" in command_line_args:
        tcdb = command_line_args["tcdb"]
    if "trackfile_sector_list" in command_line_args:
        trackfile_sector_list = command_line_args["trackfile_sector_list"]
    if "trackfiles" in command_line_args:
        trackfiles = command_line_args["trackfiles"]
    if "trackfile_parser" in command_line_args:
        trackfile_parser = command_line_args["trackfile_parser"]
    if "tc_template_yaml" in command_line_args:
        tc_template_yaml = command_line_args["tc_template_yaml"]

    # This indicates that the "area_definition" will be the definition for one
    # of the native datasets
    if (
        "self_register_dataset" in command_line_args
        and "self_register_source" in command_line_args
    ):
        self_register_dataset = command_line_args["self_register_dataset"]
        self_register_source = command_line_args["self_register_source"]

    if self_register_dataset and self_register_source:
        if (
            "area_definition" in xobjs[self_register_dataset].attrs
            and xobjs[self_register_dataset].attrs["area_definition"] is not None
        ):
            area_def = xobjs[self_register_dataset].attrs["area_definition"]
        else:
            import pyresample

            area_def = pyresample.geometry.SwathDefinition(
                lons=xobjs[self_register_dataset]["longitude"],
                lats=xobjs[self_register_dataset]["latitude"],
            )
            min_lat = xobjs[self_register_dataset]["latitude"].min()
            max_lat = xobjs[self_register_dataset]["latitude"].max()
            min_lon = xobjs[self_register_dataset]["longitude"].min()
            max_lon = xobjs[self_register_dataset]["longitude"].max()
            area_def.area_extent_ll = [min_lon, min_lat, max_lon, max_lat]
            if (
                "interpolation_radius_of_influence"
                in xobjs[self_register_dataset].attrs
            ):
                area_def.pixel_size_x = xobjs[self_register_dataset].attrs[
                    "interpolation_radius_of_influence"
                ]
                area_def.pixel_size_y = xobjs[self_register_dataset].attrs[
                    "interpolation_radius_of_influence"
                ]
            elif "sample_distance_km" in xobjs[self_register_dataset].attrs:
                area_def.pixel_size_x = xobjs[self_register_dataset].attrs[
                    "sample_distance_km"
                ]
                area_def.pixel_size_y = xobjs[self_register_dataset].attrs[
                    "sample_distance_km"
                ]

        if not hasattr(area_def, "sector_info"):
            setattr(
                area_def,
                "sector_info",
                {
                    "self_register_dataset": self_register_dataset,
                    "self_register_source": self_register_source,
                },
            )
        else:
            area_def.sector_info["self_register_dataset"] = self_register_dataset
            area_def.sector_info["self_register_source"] = self_register_source

        # Provide standard area_def information that GeoIPS expects
        if not hasattr(area_def, "sector_type"):
            setattr(area_def, "sector_type", "self_register")

        if not hasattr(area_def, "name"):
            setattr(area_def, "name", area_def.sector_type)

        if not hasattr(area_def, "area_id"):
            setattr(area_def, "area_id", area_def.name)

        if not hasattr(area_def, "description"):
            setattr(area_def, "description", area_def.name)

        # Add it to the list
        area_defs += [area_def]

    if sectorfiles:
        if xobjs is None:
            area_defs += get_static_area_defs_for_xarray(None, sectorfiles, sector_list)
        else:
            area_defs += get_static_area_defs_for_xarray(
                xobjs["METADATA"], sectorfiles, sector_list
            )
    if tcdb:
        if xobjs is None:
            raise (TypeError, "Must have xobjs defined for tcdb sectors")
        area_defs += get_tc_area_defs_for_xarray(
            xobjs["METADATA"],
            tcdb_sector_list,
            tc_template_yaml,
            aid_type="BEST",
        )
    if trackfiles:
        area_defs += get_trackfile_area_defs(
            trackfiles,
            trackfile_parser,
            trackfile_sector_list,
            tc_template_yaml,
            aid_type="BEST",
            start_datetime=xobjs["METADATA"].start_datetime - timedelta(hours=8),
            end_datetime=xobjs["METADATA"].end_datetime + timedelta(hours=3),
        )

    # If we have a "short" data file, return only a single dynamic sector closest to the start time.
    # If longer than one swath for polar orbiters, we may have more than one
    # "hit", so don't filter.
    if (
        filter_time
        and xobjs is not None
        and xobjs["METADATA"].end_datetime - xobjs["METADATA"].start_datetime
        < timedelta(hours=3)
    ):
        area_defs = filter_area_defs_actual_time(
            area_defs, xobjs["METADATA"].start_datetime
        )

    LOG.info("Allowed area_defs: %s", [ad.name for ad in area_defs])
    return list(area_defs)


def get_alg_xarray(
    sect_xarrays,
    area_def,
    product_name,
    resector=True,
    resampled_read=False,
    variable_names=None,
):
    """Get alg xarray."""
    if not variable_names:
        # original input variables from sensor.py (i.e., abi.py)
        variables = get_required_variables(
            product_name, sect_xarrays["METADATA"].source_name
        )
    else:
        # If variable_names are passed, actually use them
        # Previously was only being used for checking existence of variables in
        # sectored xarray.
        variables = variable_names

    datasets_for_vars = get_requested_datasets_for_variables(
        product_name, sect_xarrays["METADATA"].source_name
    )
    product_type = get_product_type(product_name, sect_xarrays["METADATA"].source_name)

    # Only attempt to set algorithm function if algorithm requested in product type
    if product_type in [
        "alg",
        "alg_cmap",
        "interp_alg",
        "interp_alg_cmap",
        "alg_interp_cmap",
    ]:
        alg_plugin = algorithms.get_plugin(
            get_alg_name(product_name, sect_xarrays["METADATA"].source_name)
        )
        alg_family= alg_plugin.family
        alg_args = get_alg_args(product_name, sect_xarrays["METADATA"].source_name)
    else:
        # Default to "None" so it is defined when used below.
        alg_family = None

    interp_plugin_name = get_interp_name(
        product_name, sect_xarrays["METADATA"].source_name
    )
    interp_plugin = None
    if interp_plugin_name is not None:
        interp_plugin = interpolators.get_plugin(interp_plugin_name)
        interp_args = get_interp_args(
            product_name, sect_xarrays["METADATA"].source_name
        )

    # If the initial sectoring was to a padded area definition, must sector to final area_def here.
    # Allow specifying whether it needs to be resectored or not via kwargs.
    if resector:
        curr_sect_xarrays = sector_xarrays(
            sect_xarrays,
            area_def,
            varlist=variables,
            hours_before_sector_time=6,
            hours_after_sector_time=9,
            drop=True,
        )
        # hours_before_sector_time=6, hours_after_sector_time=6, drop=True)
    else:
        curr_sect_xarrays = sect_xarrays

    LOG.info("get_alg_xarray required variables: %s", variables)
    LOG.info("get_alg_xarray requested datasets for variables: %s", datasets_for_vars)

    # If we want to run the algorithm prior to interpolation, apply the algorithm here, and return either
    # the unprojected result or interpolated result appropriately.
    if product_type in ["alg_cmap", "alg_interp_cmap", "alg"]:
        alg_xarray = xarray.Dataset()
        alg_xarray.attrs = sect_xarrays["METADATA"].attrs.copy()
        if alg_family in ["xarray_to_numpy"]:
            # Format the call signature for passing a dictionary of xarrays, plus area_def, and return a single
            # numpy array
            for dsname in sect_xarrays.keys():
                if set(variable_names).issubset(
                    set(sect_xarrays[dsname].variables.keys())
                ):
                    alg_xarray[product_name] = xarray.DataArray(
                        alg_plugin(sect_xarrays[dsname], **alg_args)
                    )
        elif alg_family in ["xarray_dict_area_def_to_numpy"]:
            # Format the call signature for passing a dictionary of xarrays, plus area_def, and return a single
            # numpy array
            alg_xarray[product_name] = xarray.DataArray(
                alg_plugin(sect_xarrays, area_def, **alg_args)
            )
        elif alg_family in ["xarray_dict_to_xarray"]:
            # Format the call signature for passing a dictionary of xarrays, plus area_def, and return a single
            # numpy array
            alg_xarray = alg_plugin(sect_xarrays, **alg_args)
        elif alg_family in ["xarray_to_xarray"]:
            input_alg_xarray = None
            for varname in variables:
                LOG.info("TRYING variable %s for non-interpolated algorithms", varname)
                for curr_sect_xarray in curr_sect_xarrays:
                    if varname in curr_sect_xarray:
                        if input_alg_xarray is None:
                            LOG.info(
                                "    USING sectored xarray %s for non-interpolated algorithms",
                                curr_sect_xarray,
                            )
                            input_alg_xarray = curr_sect_xarray
                        else:
                            LOG.info(
                                "    SKIPPING For non-interpolated data processing, all native variables must"
                                "be the same resolution! Skipping variable %s, shape %s, input_alg_xarrays: %s",
                                varname,
                                curr_sect_xarrays[varname].shape,
                                input_alg_xarray,
                            )
            if input_alg_xarray is None:
                raise ValueError(
                    'No required variables in any xarrays for "xarray_to_xarray" alg type'
                )
            alg_xarray = alg_plugin(input_alg_xarray, **alg_args)
        elif alg_family in ["list_numpy_to_numpy"]:
            # Need to pull all the required variables out of the various xarray datasets, and add them to numpy list
            # Then assign the resulting numpy array to the "product_name" DataArray
            # within the xarray Dataset
            numpys = []
            for varname in variables:
                for curr_sect_xarray in curr_sect_xarrays.values():
                    if varname in list(curr_sect_xarray.variables.keys()):
                        numpys += [curr_sect_xarray[varname].to_masked_array()]
                        alg_xarray = curr_sect_xarray
            alg_xarray[product_name] = xarray.DataArray(alg_plugin(numpys, **alg_args))

        # No interpolation required
        if product_type == "alg_cmap":
            final_xarray = alg_xarray
        # If required, interpolate the result prior to returning
        elif product_type == "alg_interp_cmap":
            interp_args["varlist"] = [product_name]
            final_xarray = interp_plugin(area_def, alg_xarray, alg_xarray, **interp_args)

        # Ensure we have the "adjustment"id" in the filename appropriately
        if "adjustment_id" in area_def.sector_info:
            final_xarray = add_filename_extra_field(
                alg_xarray, "adjustment_id", area_def.sector_info["adjustment_id"]
            )
        # return here - we are done for either alg_cmap or alg_interp_cmap type
        return final_xarray

    # NOTE if algorithm specified first in product_type, we will not get to this point!
    # Returned from above if statement

    # Default to empty xarray.Dataset() - will be populated within loop with
    # appropriate regridded variables.
    interp_xarray = xarray.Dataset()

    for varname in variables:
        LOG.info("TRYING variable %s", varname)
        for key, sect_xarray in curr_sect_xarrays.items():
            LOG.info("    TRYING dataset %s for variable %s", key, varname)

            if varname not in sect_xarray.variables:
                continue

            # Reassign interp_plugin based on CURRENT sect_xarray
            # Allow re-defining interpolation for different datasets.
            interp_plugin_name = get_interp_name(product_name, sect_xarray.source_name)
            interp_plugin = None
            if interp_plugin_name is not None:
                interp_plugin = interpolators.get_plugin(interp_plugin_name)
                interp_args = get_interp_args(product_name, sect_xarray.source_name)

            # If a specific dataset was requested for the current variable, and this dataset was NOT requested via
            # a resampled_read (in which case the native datasets won't exist, only the resampled dataset),
            # then use the appropriately requested dataset.
            if varname in datasets_for_vars and not resampled_read:
                if key in datasets_for_vars[varname]:
                    LOG.info(
                        "        USING %s varname from dataset %s, as specified in product_input YAML config",
                        varname,
                        key,
                    )
                else:
                    LOG.info(
                        "        WAITING dataset %s not requested for variable %s in product_input YAML config",
                        key,
                        varname,
                    )
                    continue
            # If we've already interpolated this variable, check if it is needed
            # before interpolating again
            elif interp_xarray is not None and varname in list(interp_xarray.keys()):
                # If all of the required variables are in the current dataset, use this
                # version
                if set(variables).issubset(set(sect_xarray.variables.keys())):
                    LOG.info(
                        "        REPLACING %s with current dataset %s, all required variables in current dataset",
                        varname,
                        key,
                    )
                # Otherwise, skip re-interpolating to avoid unecessary computation
                else:
                    LOG.warning(
                        "        SKIPPING %s, encountered multiple versions, skipping subsequent dataset %s",
                        varname,
                        key,
                    )
                    continue
            else:
                LOG.info(
                    "        USING %s varname from dataset %s - first availalbe, and not specified in YAML",
                    varname,
                    key,
                )

            # Potential efficiency hit with to_masked_array for dask arrays, etc
            # LOG.info('Min/max %s %s / %s, dataset %s',
            #          varname,
            #          sect_xarray[varname].to_masked_array().min(),
            #          sect_xarray[varname].to_masked_array().max(),
            #          key)

            # apply the requested interpolation routine.
            interp_args["varlist"] = [varname]
            if "time" in sect_xarray.dims:
                # This is for a particularly formatted dataset, that includes separate arrays for different times
                # (ABI fire product).
                # We need to be careful this does not break for some other dataset that includes a differently
                # formatted "time" dimension.
                tdims = len(sect_xarray.time)
                interp_list = [
                    interp_plugin(
                        area_def,
                        sect_xarray.isel(time=i),
                        xarray.Dataset(),
                        **interp_args,
                    )
                    for i in range(tdims)
                ]
                interp_xarray[varname] = xarray.concat(interp_list, dim="dim_2")[
                    varname
                ]
            else:
                interp_xarray = interp_plugin(
                    area_def, sect_xarray, interp_xarray, **interp_args
                )

            # Potential efficiency hit with to_masked_array for dask arrays, etc
            # LOG.info('Min/max interp %s %s / %s',
            #          varname,
            #          interp_xarray[varname].to_masked_array().min(),
            #          interp_xarray[varname].to_masked_array().max())

    # Make sure we have all the appropriate attributes attached to the current interp_xarray.
    # Use force=False so if attributes were set above, we do not overwrite them.
    copy_standard_metadata(sect_xarray, interp_xarray, force=False)

    # Specify the call signature and return value for different algorithm types:
    if product_type in ["interp"]:
        # Note "interp" product type will NOT have a single variable named "product_name", just the individual
        # interpolated variables.
        interp_xarray = interp_xarray
    elif alg_family in ["xarray_to_numpy"]:
        # xarray_to_numpy will return a single array, which can be set to the
        # "product_name" variable.
        interp_xarray[product_name] = xarray.DataArray(
            alg_plugin(interp_xarray, **alg_args)
        )
    elif alg_family in ["xarray_to_xarray"]:
        # xarray_to_xarray algorithm type will return the full xarray object - assume variable names have been
        # set appropriately within the algorithm.  This could be another good use of the "alt_varname_for_covg"
        # kwarg in the coverage checks - if we want to just use a specific variable for the coverage checks rather
        # than the "product_name" variable.
        interp_xarray = alg_plugin(interp_xarray, **alg_args)
    elif alg_family in [
        "single_channel",
        "channel_combination",
        "list_numpy_to_numpy",
        "rgb",
    ]:
        # Assume ANYTHING else takes in a list of numpy arrays, and returns a single numpy array.
        # Perhaps we should be explicit here...
        interp_xarray[product_name] = xarray.DataArray(
            alg_plugin(
                [interp_xarray[varname].to_masked_array() for varname in variables],
                **alg_args,
            )
        )
    else:
        raise TypeError(
            f'UNSUPPORTED alg_family "{alg_family}" or product_type "{product_type}", '
            'please add to geoips/interface_modules/procflows/single_source.py "get_alg_xarray" '
            "function appropriately"
        )

    # Make sure we have all the appropriate attributes attached to the current interp_xarray.
    # Use force=False so if attributes were set above, we do not overwrite them.
    copy_standard_metadata(sect_xarray, interp_xarray, force=False)
    # Attach final product_name to the interp_xarray as well (the end goal of
    # this routine)
    interp_xarray.attrs["product_name"] = product_name
    # Add appropriate attributes to alg_xarray
    if "adjustment_id" in area_def.sector_info:
        interp_xarray = add_filename_extra_field(
            interp_xarray, "adjustment_id", area_def.sector_info["adjustment_id"]
        )

    return interp_xarray


def verify_area_def(
    area_defs,
    check_area_def,
    data_start_datetime,
    data_end_datetime,
    time_range_hours=3,
):
    """Verify area def."""
    from geoips.sector_utils.utils import filter_area_defs_actual_time

    if data_end_datetime - data_start_datetime < timedelta(hours=time_range_hours):
        new_area_defs = filter_area_defs_actual_time(area_defs, data_start_datetime)
    LOG.info("Allowed area_defs: %s", [ad.name for ad in new_area_defs])
    if check_area_def.name not in [ad.name for ad in new_area_defs]:
        return False
    return True


def single_source(fnames, command_line_args=None):
    """Workflow for running products from a single data source.

    Parameters
    ----------
    fnames : list
        List of strings specifying full paths to input file names to process
    command_line_args : dict
        dictionary of command line arguments

    Returns
    -------
    list
        Return list of strings specifying full paths to output products that
        were produced

    See Also
    --------
    ``geoips.commandline.args``
        Complete list of available command line args.
    """
    from datetime import datetime

    process_datetimes = {}
    process_datetimes["overall_start"] = datetime.utcnow()
    final_products = []
    removed_products = []
    saved_products = []
    database_writes = []

    from geoips.commandline.args import check_command_line_args

    # These args should always be checked
    check_args = [
        "sector_list",
        "sectorfiles",  # Static sectors,
        "tcdb",
        "tcdb_sector_list",  # TC Database sectors,
        "trackfiles",
        "trackfile_parser",
        "trackfile_sector_list",  # Flat text trackfile,
        "reader_name",
        "product_name",
        "gridlines_params",
        "boundaries_params",
        "product_params_override",
        "output_format",
        "filename_format",
        "output_format_kwargs",
        "filename_format_kwargs",
        "metadata_output_format",
        "metadata_filename_format",
        "metadata_output_format_kwargs",
        "metadata_filename_format_kwargs",
        "adjust_area_def",
        "reader_defined_area_def",
        "self_register_source",
        "self_register_dataset",
        "sectored_read",
        "resampled_read",
        "product_db",
    ]

    check_command_line_args(check_args, command_line_args)

    product_name = command_line_args["product_name"]  # 89HNearest
    output_format = command_line_args[
        "output_format"
    ]  # output_formats.imagery_annotated
    reader_name = command_line_args["reader_name"]  # ssmis_binary
    compare_path = command_line_args["compare_path"]
    output_file_list_fname = command_line_args["output_file_list_fname"]
    compare_outputs_module = command_line_args["compare_outputs_module"]
    adjust_area_def = command_line_args["adjust_area_def"]
    self_register_source = command_line_args["self_register_source"]
    self_register_dataset = command_line_args["self_register_dataset"]
    reader_defined_area_def = command_line_args["reader_defined_area_def"]
    sectored_read = command_line_args["sectored_read"]
    resampled_read = command_line_args["resampled_read"]
    product_db = command_line_args["product_db"]
    product_db_writer = command_line_args["product_db_writer"]

    if product_db:
        from geoips_db.dev.postgres_database import get_db_writer

        db_writer = get_db_writer(product_db_writer)
        if not os.getenv("G2DB_USER") or not os.getenv("G2DB_PASS"):
            raise ValueError("Need to set both $G2DB_USER and $G2DB_PASS")

    from geoips.interfaces import readers

    reader = readers.get_plugin(reader_name)
    print_mem_usage("MEMUSG", verbose=False)

    num_jobs = 0
    xobjs = reader(fnames, metadata_only=True)
    print_mem_usage("MEMUSG", verbose=False)

    variables = get_required_variables(
        product_name, xobjs["METADATA"].source_name
    )  # get input variables
    product_type = get_product_type(product_name, xobjs["METADATA"].source_name)

    # If we need to pull area_defs from the reader, then we need to read in
    # order to determin what to run
    if (not sectored_read and not resampled_read) and (
        reader_defined_area_def or (self_register_source and self_register_dataset)
    ):
        xobjs = reader(fnames, metadata_only=False, chans=variables)

    # Use the xarray objects and command line args to determine required area_defs
    print_mem_usage("MEMUSG", verbose=False)
    area_defs = get_area_defs_from_command_line_args(
        command_line_args, xobjs, variables, filter_time=True
    )

    # If we do not need to pull area_defs from the reader, read the data AFTER
    # we determine we have areas to run
    if area_defs and (
        not reader_defined_area_def
        and not self_register_source
        and not sectored_read
        and not resampled_read
    ):
        print_mem_usage("MEMUSG", verbose=False)
        xobjs = reader(fnames, metadata_only=False, chans=variables)

    print_mem_usage("MEMUSG", verbose=False)
    # If we have a product of type "unsectored_xarray_dict_to_output_format" process it here
    # This will not have any required area_defs
    if product_type == "unsectored_xarray_dict_to_output_format":
        xdict = reader(fnames, metadata_only=False)
        final_products += process_xarray_dict_to_output_format(
            xdict, variables, product_name, command_line_args
        )
    elif product_type == "unsectored_xarray_dict_area_to_output_format":
        xdict = reader(fnames, metadata_only=False)

    print_mem_usage("MEMUSG", verbose=False)
    from geoips.filenames.duplicate_files import remove_duplicates

    new_attrs = {"filename_extra_fields": {}}
    # setup for TC products
    for area_def in area_defs:
        if product_type == "unsectored_xarray_dict_area_to_output_format":
            final_products += process_xarray_dict_to_output_format(
                xdict, variables, product_name, command_line_args, area_def
            )
            continue

        LOG.info("\n\n\n\nNEXT area definition: %s", area_def)
        pad_area_def = pad_area_definition(area_def, xobjs["METADATA"].source_name)

        # Only attempt to read within the area_def loop if we have requested
        # "sectored_read" or "resampled_read"
        if sectored_read or resampled_read:
            try:
                xobjs = reader(
                    fnames, metadata_only=False, chans=variables, area_def=pad_area_def
                )
            # geostationary satellites fail with IndexError when the area_def does not intersect the
            # data.  Just skip those.  We need a better method for handling this generally, but for
            # now skip IndexErrors.
            except IndexError as resp:
                LOG.error("SKIPPING no coverage for %s, %s", area_def.name, str(resp))
                continue

        process_datetimes[area_def.area_id] = {}
        process_datetimes[area_def.area_id]["start"] = datetime.utcnow()
        # add SatAzimuth and SunAzimuth into list of the variables for ABI only (come from ABI reader)
        # if xobjs['METADATA'].source_name == 'abi':
        #     if 'SatAzimuth' in list(xobjs.values())[0].keys() and 'SunAzimuth' in list(xobjs.values())[0].keys():
        #         variables +=['SatAzimuth', 'SunAzimuth']
        #     else:
        #         raise ValueError('SatAzimuth and/or SunAzimuth not in ABI data')
        if area_def.sector_type in ["reader_defined", "self_register"]:
            LOG.info("CONTINUE Not sectoring sector_type %s", area_def.sector_type)
            pad_sect_xarrays = xobjs
        else:
            pad_sect_xarrays = sector_xarrays(
                xobjs,
                pad_area_def,
                varlist=variables,
                hours_before_sector_time=6,
                hours_after_sector_time=9,
                drop=True,
            )

        print_mem_usage("MEMUSG", verbose=False)
        if len(pad_sect_xarrays.keys()) == 0:
            LOG.info("SKIPPING no sectored xarrays returned for %s", area_def.name)
            continue

        if not verify_area_def(
            area_defs,
            pad_area_def,
            pad_sect_xarrays["METADATA"].start_datetime,
            pad_sect_xarrays["METADATA"].end_datetime,
        ):
            LOG.info(
                "SKIPPING duplicate area_def, out of time range, for %s", area_def.name
            )
            continue

        curr_output_products = process_sectored_data_output(
            pad_sect_xarrays,
            variables,
            product_name,
            command_line_args,
            area_def=area_def,
        )

        print_mem_usage("MEMUSG", verbose=False)
        # If we had a request for sectored data processing, skip the rest of the loop
        if curr_output_products:
            final_products += curr_output_products
            continue

        if adjust_area_def:
            from geoips.geoips_utils import find_entry_point

            area_def_adjuster = find_entry_point("area_def_adjusters", adjust_area_def)
            area_def_adjuster_type = getattr(
                import_module(area_def_adjuster.__module__), "adjuster_type"
            )
            # Use normal size sectored xarray when running area_def_adjuster, not padded
            # Center time (mintime + (maxtime - mintime)/2) is very slightly different for different size
            # sectored arrays, so for consistency if we change padding amounts, use the fully sectored
            # array for adjusting the area_def.
            if pad_sect_xarrays["METADATA"].source_name not in ["amsu-b", "mhs"]:
                if area_def.sector_type in ["reader_defined", "self_register"]:
                    LOG.info(
                        "CONTINUE Not sectoring sector_type %s", area_def.sector_type
                    )
                    sect_xarrays = pad_sect_xarrays
                else:
                    sect_xarrays = sector_xarrays(
                        pad_sect_xarrays,
                        area_def,
                        varlist=variables,
                        hours_before_sector_time=6,
                        hours_after_sector_time=9,
                        drop=True,
                    )
                if (
                    area_def_adjuster_type
                    == "list_xarray_list_variables_to_area_def_out_fnames"
                ):
                    area_def, adadj_fnames = area_def_adjuster(
                        list(sect_xarrays.values()), area_def, variables
                    )
                else:
                    area_def = area_def_adjuster(
                        list(sect_xarrays.values()), area_def, variables
                    )
            else:
                # AMSU-b specifically needs full swath width...
                if (
                    area_def_adjuster_type
                    == "list_xarray_list_variables_to_area_def_out_fnames"
                ):
                    area_def, adadj_fnames = area_def_adjuster(
                        list(pad_sect_xarrays.values()), area_def, variables
                    )
                else:
                    area_def = area_def_adjuster(
                        list(pad_sect_xarrays.values()), area_def, variables
                    )
            # These will be added to the alg_xarray
            # new_attrs['area_definition'] = area_def
            if "adjustment_id" in area_def.sector_info:
                new_attrs["filename_extra_fields"][
                    "adjustment_id"
                ] = area_def.sector_info["adjustment_id"]

        print_mem_usage("MEMUSG", verbose=False)
        all_vars = []
        for key, xobj in pad_sect_xarrays.items():
            # Double check the xarray object actually contains data
            for var in list(xobj.variables.keys()):
                if xobj[var].count() > 0:
                    all_vars.append(var)
        # If the required variables are not contained within the xarray objects, do not
        # attempt to process (variables in product algorithm are not available)
        if set(variables).issubset(all_vars):

            # We want to write out the padded xarray for "xarray_data" output types
            # Otherwise, we need the fully sectored output
            output_plugin = output_formats.get_plugin(output_format)
            if output_plugin.family == "xarray_data":
                alg_xarray = get_alg_xarray(
                    pad_sect_xarrays,
                    pad_area_def,
                    product_name,
                    resector=False,
                    resampled_read=resampled_read,
                )
            elif area_def.sector_type in ["reader_defined", "self_register"]:
                alg_xarray = get_alg_xarray(
                    pad_sect_xarrays,
                    pad_area_def,
                    product_name,
                    resector=False,
                    resampled_read=resampled_read,
                    variable_names=variables,
                )
            else:
                alg_xarray = get_alg_xarray(
                    pad_sect_xarrays,
                    area_def,
                    product_name,
                    resector=True,
                    resampled_read=resampled_read,
                )

            print_mem_usage("MEMUSG", verbose=False)

            # This defaults to "covg_func" and "covg_args" - if
            # image_production_covg_* exist, it will use those.
            covg_func = get_covg_from_product(
                product_name,
                alg_xarray.source_name,
                output_dict=command_line_args,
                covg_func_field_name="image_production_covg_func",
            )
            covg_args = get_covg_args_from_product(
                product_name,
                alg_xarray.source_name,
                output_dict=command_line_args,
                covg_args_field_name="image_production_covg_args",
            )
            covg = covg_func(alg_xarray, product_name, area_def, **covg_args)

            fname_covg_func = get_covg_from_product(
                product_name,
                alg_xarray.source_name,
                output_dict=command_line_args,
                covg_func_field_name="fname_covg_func",
            )
            fname_covg_args = get_covg_args_from_product(
                product_name,
                alg_xarray.source_name,
                output_dict=command_line_args,
                covg_args_field_name="fname_covg_args",
            )
            fname_covg = fname_covg_func(
                alg_xarray, product_name, area_def, **fname_covg_args
            )

            for attrname in new_attrs:
                LOG.info(
                    "ADDING attribute %s %s to alg_xarray",
                    attrname,
                    new_attrs[attrname],
                )
                alg_xarray.attrs[attrname] = new_attrs[attrname]

            # Apply a new coverage scheme (coverage within 300km radical range from TC center)
            # to be done  ????

            minimum_coverage = 10
            command_line_minimum_coverage = get_minimum_coverage(
                product_name, command_line_args
            )
            if hasattr(alg_xarray, "minimum_coverage"):
                minimum_coverage = alg_xarray.minimum_coverage
            if command_line_minimum_coverage is not None:
                minimum_coverage = command_line_minimum_coverage
            LOG.info(
                "Required coverage %s for product %s, actual coverage %s",
                minimum_coverage,
                product_name,
                covg,
            )
            if covg < minimum_coverage and fname_covg < minimum_coverage:
                LOG.info(
                    "Insufficient coverage %s / %s for data products for %s, %s required SKIPPING",
                    covg,
                    fname_covg,
                    area_def.name,
                    minimum_coverage,
                )
                continue

            output_format_kwargs = get_output_format_kwargs(
                command_line_args, xarray_obj=alg_xarray, area_def=area_def
            )

            curr_products = plot_data(
                command_line_args,
                alg_xarray,
                area_def,
                product_name,
                output_format_kwargs,
            )

            print_mem_usage("MEMUSG", verbose=False)
            final_products += curr_products
            curr_removed_products, curr_saved_products = remove_duplicates(
                curr_products, remove_files=True
            )
            removed_products += curr_removed_products
            saved_products += curr_saved_products

            if product_db:
                for fprod, fname_fmt in curr_products.items():
                    additional_attrs = {
                        "coverage": covg,
                        "product": product_name,
                        "fileType": fprod.split(".")[-1],
                    }
                    product_added = db_writer(
                        fprod, area_def, alg_xarray, additional_attrs=additional_attrs
                    )
                    database_writes += [product_added]

            process_datetimes[area_def.area_id]["end"] = datetime.utcnow()
            num_jobs += 1
        else:
            LOG.info(
                'SKIPPING No coverage or required variables "%s" for %s %s',
                variables,
                xobjs["METADATA"].source_name,
                area_def.name,
            )
            # raise ImportError('Failed to find required fields in product algorithm: {0}.{1}'.format(
            #                                                        sect_xarrays[0].source_name,product_name))

    process_datetimes["overall_end"] = datetime.utcnow()

    LOG.info(
        "The following products were produced from procflow %s", os.path.basename(__file__)
    )
    for output_product in final_products:
        LOG.info("    SINGLESOURCESUCCESS %s", output_product)
        if output_product in database_writes:
            LOG.info("    DATABASESUCCESS %s", output_product)

    for removed_product in removed_products:
        LOG.info("    DELETEDPRODUCT %s", removed_product)

    if output_file_list_fname:
        LOG.info("Writing successful outputs to %s", output_file_list_fname)
        with open(output_file_list_fname, "w", encoding="utf8") as fobj:
            fobj.writelines(
                "\n".join(
                    [
                        fname.replace(gpaths["GEOIPS_OUTDIRS"], "$GEOIPS_OUTDIRS")
                        for fname in final_products
                    ]
                )
            )
            # If we don't write out the last newline, then wc won't return the appropriate number, and we won't get
            # to the last file when attempting to loop through
            fobj.writelines(["\n"])

    retval = 0
    if compare_path:
        from geoips.geoips_utils import find_entry_point

        compare_outputs = find_entry_point("output_comparisons", compare_outputs_module)
        retval = compare_outputs(
            compare_path.replace("<product>", product_name).replace(
                "<procflow>", "single_source"
            ),
            final_products,
        )

    print_mem_usage("MEMUSG", verbose=True)
    LOG.info("READER_NAME: %s", reader_name)
    LOG.info("PRODUCT_NAME: %s", product_name)
    LOG.info("NUM_PRODUCTS: %s", len(final_products))
    LOG.info("NUM_DELETED_PRODUCTS: %s", len(removed_products))
    output_process_times(process_datetimes, num_jobs, job_str="single_source procflow")
    return retval
