# MODULE:    grass.utils
#
# AUTHOR(S): Vaclav Petras <wenzeslaus gmail com>
#
# PURPOSE:   Collection of various helper general (non-GRASS) utilities
#
# COPYRIGHT: (C) 2021 Vaclav Petras, and by the GRASS Development Team
#
#            This program is free software under the GNU General Public
#            License (>=v2). Read the file COPYING that comes with GRASS
#            for details.

"""Download and extract various archives"""

import http
import os
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import urlretrieve
from urllib import request as urlrequest

HEADERS = {
    "User-Agent": "Mozilla/5.0",
}
HTTP_STATUS_CODES = list(http.HTTPStatus)


def debug(*args, **kwargs):
    """Print a debug message (to be used in this module only)

    Using the stanard grass.script debug function is nice, but it may create a circular
    dependency if this is used from grass.script, so this is a wrapper which lazy
    imports the standard function.
    """
    # Lazy import to avoiding potential circular dependency.
    import grass.script as gs  # pylint: disable=import-outside-toplevel

    gs.debug(*args, **kwargs)


class DownloadError(Exception):
    """Error happened during download or when processing the file"""


# modified copy from g.extension
# TODO: Possibly migrate to shutil.unpack_archive.
def extract_tar(name, directory, tmpdir):
    """Extract a TAR or a similar file into a directory"""
    debug(
        f"extract_tar(name={name}, directory={directory}, tmpdir={tmpdir})",
        3,
    )
    try:
        tar = tarfile.open(name)
        extract_dir = os.path.join(tmpdir, "extract_dir")
        os.mkdir(extract_dir)

        # Extraction filters were added in Python 3.12,
        # and backported to 3.8.17, 3.9.17, 3.10.12, and 3.11.4
        # See
        # https://docs.python.org/3.12/library/tarfile.html#tarfile-extraction-filter
        # and https://peps.python.org/pep-0706/
        # In Python 3.12, using `filter=None` triggers a DepreciationWarning,
        # and in Python 3.14, `filter='data'` will be the default
        if hasattr(tarfile, "data_filter"):
            tar.extractall(path=extract_dir, filter="data")
        else:
            # Remove this when no longer needed
            debug(_("Extracting may be unsafe; consider updating Python"))
            tar.extractall(path=extract_dir)

        files = os.listdir(extract_dir)
        _move_extracted_files(
            extract_dir=extract_dir, target_dir=directory, files=files
        )
    except tarfile.TarError as error:
        raise DownloadError(
            _("Archive file is unreadable: {0}").format(error)
        ) from error
    except EOFError as error:
        raise DownloadError(
            _("Archive file is incomplete: {0}").format(error)
        ) from error


extract_tar.supported_formats = ["tar.gz", "gz", "bz2", "tar", "gzip", "targz", "xz"]


# modified copy from g.extension
# TODO: Possibly migrate to shutil.unpack_archive.
def extract_zip(name, directory, tmpdir):
    """Extract a ZIP file into a directory"""
    debug(
        f"extract_zip(name={name}, directory={directory}, tmpdir={tmpdir})",
        3,
    )
    try:
        zip_file = zipfile.ZipFile(name, mode="r")
        file_list = zip_file.namelist()
        # we suppose we can write to parent of the given dir
        # (supposing a tmp dir)
        extract_dir = os.path.join(tmpdir, "extract_dir")
        os.mkdir(extract_dir)
        for subfile in file_list:
            # this should be safe in Python 2.7.4
            zip_file.extract(subfile, extract_dir)
        files = os.listdir(extract_dir)
        _move_extracted_files(
            extract_dir=extract_dir, target_dir=directory, files=files
        )
    except zipfile.BadZipfile as error:
        raise DownloadError(_("ZIP file is unreadable: {0}").format(error))


# modified copy from g.extension
def _move_extracted_files(extract_dir, target_dir, files):
    """Fix state of extracted file by moving them to different directory

    When extracting, it is not clear what will be the root directory
    or if there will be one at all. So this function moves the files to
    a different directory in the way that if there was one directory extracted,
    the contained files are moved.
    """
    debug("_move_extracted_files({})".format(locals()))
    if len(files) == 1:
        actual_path = os.path.join(extract_dir, files[0])
        if os.path.isdir(actual_path):
            shutil.copytree(actual_path, target_dir)
        else:
            shutil.copy(actual_path, target_dir)
    else:
        if not os.path.exists(target_dir):
            os.mkdir(target_dir)
        for file_name in files:
            actual_file = os.path.join(extract_dir, file_name)
            if os.path.isdir(actual_file):
                # Choice of copy tree function:
                # shutil.copytree() fails when subdirectory exists.
                # However, distutils.copy_tree() may fail to create directories before
                # copying files into them when copying to a recently deleted directory.
                shutil.copytree(actual_file, os.path.join(target_dir, file_name))
            else:
                shutil.copy(actual_file, os.path.join(target_dir, file_name))


# modified copy from g.extension
# TODO: remove the hardcoded location/extension, use general name
def download_and_extract(source, reporthook=None):
    """Download a file (archive) from URL and extract it

    Call urllib.request.urlcleanup() to clean up after urlretrieve if you terminate
    this function from another thread.
    """
    source_path = Path(urlparse(source).path)
    tmpdir = tempfile.mkdtemp()
    debug("Tmpdir: {}".format(tmpdir))
    directory = Path(tmpdir) / "extracted"
    http_error_message = _("Download file from <{url}>, return status code {code}, ")
    url_error_message = _(
        "Download file from <{url}>, failed. Check internet connection."
    )
    if source_path.suffix and source_path.suffix == ".zip":
        archive_name = os.path.join(tmpdir, "archive.zip")
        try:
            filename, headers = urlretrieve(source, archive_name, reporthook)
        except HTTPError as err:
            raise DownloadError(
                http_error_message.format(
                    url=source,
                    code=err,
                ),
            )
        except URLError:
            raise DownloadError(url_error_message.format(url=source))
        if headers.get("content-type", "") != "application/zip":
            raise DownloadError(
                _(
                    "Download of <{url}> failed or file <{name}> is not a ZIP file"
                ).format(url=source, name=filename)
            )
        extract_zip(name=archive_name, directory=directory, tmpdir=tmpdir)
    elif source_path.suffix and source_path.suffix[1:] in extract_tar.supported_formats:
        ext = "".join(source_path.suffixes)
        archive_name = os.path.join(tmpdir, "archive" + ext)
        try:
            urlretrieve(source, archive_name, reporthook)
        except HTTPError as err:
            raise DownloadError(
                http_error_message.format(
                    url=source,
                    code=err,
                ),
            )
        except URLError:
            raise DownloadError(url_error_message.format(url=source))
        extract_tar(name=archive_name, directory=directory, tmpdir=tmpdir)
    else:
        # probably programmer error
        raise DownloadError(_("Unknown format '{}'.").format(source))
    return directory


def name_from_url(url):
    """Extract name from URL"""
    name = os.path.basename(urlparse(url).path)
    name = os.path.splitext(name)[0]
    if name.endswith(".tar"):
        # Special treatment of .tar.gz extension.
        return os.path.splitext(name)[0]
    return name


def urlopen(url, *args, **kwargs):
    """Wrapper around urlopen. Same function as 'urlopen', but with the
    ability to define headers.

    :param str url: URL

    :return: urllib.request.urlopen response object
    """
    proxy = kwargs.get("proxy")
    if proxy:
        del kwargs["proxy"]
        PROXIES = {}
        for ptype, purl in (p.split("=") for p in proxy.split(",")):
            PROXIES[ptype] = purl
        proxy = urlrequest.ProxyHandler(PROXIES)
        opener = urlrequest.build_opener(proxy)
        urlrequest.install_opener(opener)
    request = urlrequest.Request(url, headers=HEADERS)
    return urlrequest.urlopen(request, *args, **kwargs)


def download_file(url, response_format, file_name, *args, **kwargs):
    """Download file

    :param str url: file URL address
    :param str response_format: content type of downloaded file
    :param str file_name: downloaded file name

    :return: urllib.request.urlopen response object

    >>> grass_version = os.getenv("GRASS_VERSION", "unknown")
    >>> if grass_version != "unknown":
    ...     major, minor, patch = grass_version.split(".")
    ...     url = (
    ...               "https://grass.osgeo.org/addons/grass{}/"
    ...               "modules.xml".format(major)
    ...     )
    ...     response = download_file(
    ...         url=url,
    ...         response_format="application/xml",
    ...         file_name=os.path.basename(urlparse(url).path),
    ...     ) # doctest: +SKIP
    ...     response.code # doctest: +SKIP
    200
    """
    import grass.script as gs

    try:
        response = urlopen(url, *args, **kwargs)

        if not response.code == 200:
            index = HTTP_STATUS_CODES.index(response.code)
            desc = HTTP_STATUS_CODES[index].description
            gs.fatal(
                _(
                    "The download of the <{file_name}> file "
                    " from the server <{url}> was not successful"
                    " return status code {code},"
                    " {desc}".format(
                        file_name=file_name,
                        url=url,
                        code=response.code,
                        desc=desc,
                    ),
                ),
            )
        if response_format not in response.getheader("Content-Type"):
            gs.fatal(
                _(
                    "Wrong downloaded <{file_name}> format."
                    " Check url <{url}>. Allowed file format is"
                    " {response_format}.".format(
                        file_name=file_name,
                        url=url,
                        response_format=response_format,
                    ),
                ),
            )
        return response
    except (HTTPError, URLError) as err:
        desc = ""
        if hasattr(err, "code"):
            index = HTTP_STATUS_CODES.index(err.code)
            desc = ", {}".format(HTTP_STATUS_CODES[index].description)
        gs.fatal(
            _(
                "The download of the <{file_name}> file"
                " from the server <{url}> was not successful, "
                " {err}{desc}.".format(
                    file_name=file_name,
                    url=url,
                    err=err,
                    desc=desc,
                ),
            ),
        )
