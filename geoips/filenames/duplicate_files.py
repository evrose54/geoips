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

"""Module to handle removing duplicate files, based on filename formats.

If an individual filename format has a method named
``"<filename_format>_remove_duplicates"``
defined, use that method to remove duplicates for the given current filename.
"""

import logging

LOG = logging.getLogger(__name__)


def remove_duplicates(fnames, remove_files=False):
    """Remove duplicate files from all filenames included in dict fnames.

    Parameters
    ----------
    fnames : dict
        Dictionary with individual filenames as keys, and a field named
        "filename_format" which indicates the filename format used to
        generate the given filename.
    remove_files : bool, optional
        Specify whether to remove files (True), or just list what would have
        been removed, default to False

    Returns
    -------
    removed_files : list
        List of files that were removed.
    saved_files : list
        List of files that were not removed.
    """
    removed_files = []
    saved_files = []
    from geoips.sector_utils.utils import is_sector_type
    from geoips.interfaces import filename_formats
    from importlib import import_module

    for fname in fnames:
        filename_format = fnames[fname]["filename_format"]
        fname_fmt_plugin = filename_formats.get_plugin(fnames[fname]["filename_format"])
        if hasattr(
            import_module(fname_fmt_plugin.__module__),
            f"{filename_format}_remove_duplicates"
        ):
            fnamer_remove_dups = getattr(
                import_module(fname_fmt_plugin.__module__), f"{filename_format}_remove_duplicates"
            )
            curr_removed_files, curr_saved_files = fnamer_remove_dups(
                fname, remove_files=remove_files
            )
            removed_files += curr_removed_files
            saved_files += curr_saved_files
        else:
            LOG.warning(
                f"SKIPPING DUPLICATE REMOVAL no {filename_format}_remove_duplicates defined"
            )

    return removed_files, saved_files
