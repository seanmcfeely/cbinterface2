"""Functions that work with query related Carbon Black APIs.
"""

import time
import datetime
import logging
from dateutil import tz

from typing import Union

from cbc_sdk.rest_api import CBCloudAPI
from cbc_sdk.platform import Process
from cbc_sdk.platform.processes import AsyncProcessQuery

LOGGER = logging.getLogger("cbinterface.psc2.query")


def is_valid_process_query(query: AsyncProcessQuery) -> bool:
    """Custom query validation.

    Args:
        query: Cb threathunter query
    Returns:
        True if a valid query, else False.
    """
    if not query._doc_class.validation_url:
        LOGGER.debug("Unexpectedly, class has no validation url. hmm...")
        return True

    url = query._doc_class.validation_url.format(query._cb.credentials.org_key)

    args = query._get_query_parameters()
    if args.get("query", False):
        args["q"] = args["query"]
    # v2 search sort key does not work with v1 validation
    args.pop("sort", None)
    LOGGER.debug(f"attempting to validate query with args: {args}")

    validated = query._cb.get_object(url, query_parameters=args)
    if not validated.get("valid"):
        LOGGER.error(f'Invalud query {validated["invalid_message"]}')
        return False
    return True

class CustomAsyncProcessQuery(AsyncProcessQuery):
    # Created this custom object to pull additional FIELDS***
    # https://developer.carbonblack.com/reference/carbon-black-cloud/platform/latest/platform-search-fields/#additional-indicators
    def __init__(self, doc_class, cb):
        super().__init__(doc_class, cb)

    def _submit(self):
        # XXX TODO: Open a Github issue asking them to allow for fields spec customization
        if self._query_token:
            raise ApiError("Query already submitted: token {0}".format(self._query_token))

        args = self._get_query_parameters()
        args["fields"] = [
            "*",
            "parent_hash",
            "process_sha256",
            "parent_name",
            "process_cmdline",
            "backend_timestamp",
            "device_external_ip",
            "device_group",
            "device_internal_ip",
            "device_os",
            "device_policy",
            "process_effective_reputation",
            "process_reputation",
            "process_start_time",
            "process_service_name",
            "ttp"
        ]
        # use is_valid_process_query instead
        #self._validate(args)

        url = f"/api/investigate/v2/orgs/{self._cb.credentials.org_key}/processes/search_jobs"
        query_start = self._cb.post_object(url, body=args)

        self._query_token = query_start.json().get("job_id")

        self._timed_out = False
        self._submit_time = time.time() * 1000
        # added
        return self

def make_process_query(
    cb: CBCloudAPI, query: str, start_time: datetime.datetime = None, last_time: datetime.datetime = None
) -> AsyncProcessQuery:
    """Query the CBCloudAPI environment and interface results.

    Args:
        cb: A CBCloudAPI object to use
        query: The process query
        start_time: Set the process start time (UTC).
        last_time: Set the process last time (UTC). Only processes with a start
        time that falls before this last_time.
    Returns: AsyncProcessQuery or empty list.
    """

    LOGGER.debug(f"buiding query: {query} between '{start_time}' and '{last_time}'")
    processes = []
    try:
        processes = CustomAsyncProcessQuery(Process, cb).where(query)
        if not is_valid_process_query(processes):
            LOGGER.info(f"For help, refer to {cb.url}/#userGuideLocation=search-guide/investigate-th&fullscreen")
            return []
        # go ahead and submit the search job and wait for it to complete and load the _total_results
        processes._submit()._count()
        if start_time or last_time:
            start_time = start_time.isoformat() if start_time else "*"
            end_time = last_time.isoformat() if last_time else "*"
            # TODO make sure field is still correct
            processes = processes.where(f"process_start_time:[{start_time} TO {end_time}]")
        LOGGER.info(f"got {processes._total_results} process results.")
    except Exception as e:
        LOGGER.error(f"unexpected exception: {e}")

    return processes

# XXX remove?
def submit_process_query_search_job(
    cb: CBCloudAPI, query: str, start_time: datetime.datetime = None, last_time: datetime.datetime = None) -> int:

    args = {}
    url = f"/api/investigate/v2/orgs/{self._cb.credentials.org_key}/processes/search_jobs"


def print_facet_histogram(processes: AsyncProcessQuery):
    """Print facets"""
    # NOTE, this is a custom implementations. TODO, look at using the built in
    # API methods: https://developer.carbonblack.com/reference/carbon-black-cloud/cb-threathunter/latest/process-search-v2/#start-a-process-facet-job
    # Also, NOTE that this table lists fields that support faceting via the built in method, children is not one of them:
    # https://developer.carbonblack.com/reference/cb-threathunter/latest/process-search-fields/
    from cbinterface.helpers import create_histogram_string, get_os_independant_filepath

    fields = [
        "parent_name",
        "process_name",
        "process_reputation",
        "process_username",
        "process_sha256",
        "device_name",
        "device_os",
    ]
    path_fields = ["parent_name", "process_name"]
    processes = list(processes)
    facet_dict = {}
    for field_name in fields:
        facet_dict[field_name] = {}
        for proc in processes:
            value = proc.get(field_name, "None")
            if isinstance(value, list):
                if len(value) > 1:
                    LOGGER.info(f"condensing {value} to {value[0]}")
                value = value[0]
            elif field_name in path_fields:
                file_path = get_os_independant_filepath(value)
                file_name = file_path.name
                value = file_name
            if value not in facet_dict[field_name]:
                facet_dict[field_name][value] = 1
            else:
                facet_dict[field_name][value] += 1

    # special case for "children"
    try:
        facet_dict["childproc_name"] = {}
        depth = 0
        for proc in processes:
            if proc.childproc_count < 1:
                continue
            children = proc.summary.children or []
            for cp in children:
                process_path = get_os_independant_filepath(cp.get("process_name"))
                process_name = process_path.name
                if process_name not in facet_dict["childproc_name"]:
                    facet_dict["childproc_name"][process_name] = 1
                else:
                    facet_dict["childproc_name"][process_name] += 1
    except Exception as e:
        LOGGER.warning(f"problem enumerating child process names: {e}")

    print("\n------------------------- FACET HISTOGRAMS -------------------------")
    for field_name, facets in facet_dict.items():
        print(f"\n\t{field_name} results: {len(facets.keys())}")
        print("\t--------------------------------")
        print(create_histogram_string(facets))
    return


def print_facet_histogram_v2(
    cb: CBCloudAPI, query: str, start_time: datetime.datetime = None, end_time: datetime.datetime = None
):
    """Get query facet results from the CbAPI enriched events facets."""

    # NOTE: no support for childproc facets with this built-in

    from cbinterface.helpers import get_os_independant_filepath

    post_data = {}
    post_data["query"] = query
    fields = [
        "parent_name",
        "process_name",
        "process_reputation",
        "process_username",
        "process_sha256",
        "device_name",
        "device_os",
    ]
    path_fields = ["parent_name", "process_name"]
    post_data["terms"] = {"fields": fields}
    post_data["time_range"] = {}
    if start_time:
        post_data["time_range"]["start"] = start_time.isoformat()
    if end_time:
        post_data["time_range"]["end"] = end_time.isoformat()

    # TODO handle status_code!=200 and response is not json for both requests

    uri = f"/api/investigate/v2/orgs/{cb.credentials.org_key}/processes/facet_jobs"
    job_id = cb.post_object(uri, post_data).json().get("job_id", None)
    if not job_id:
        LOGGER.error(f"failed to get facet job.")
        return False

    uri = f"/api/investigate/v2/orgs/{cb.credentials.org_key}/processes/facet_jobs/{job_id}/results"
    facet_data = cb.get_object(uri)

    print("\n------------------------- FACET HISTOGRAMS -------------------------")
    total = facet_data["num_found"]
    for facets in facet_data["terms"]:
        field_name = facets["field"]
        print(f"\n\t{field_name} results: {len(facets['values'])}")
        print("\t--------------------------------")
        for entry in facets["values"]:
            entry_name = entry["name"]
            if field_name in path_fields and len(entry_name) > 55:
                file_path = get_os_independant_filepath(entry_name)
                file_name = file_path.name
                file_path = entry_name[: len(entry_name) - len(file_name)]
                file_path = file_path[: 40 - len(file_name)]
                entry_name = f"{file_path}...{file_name}"
            bar_value = int(((entry["total"] / total) * 100) / 2)
            print(
                "%30s: %5s %5s%% %s"
                % (entry_name, entry["total"], int(entry["total"] / total * 100), "\u25A0" * bar_value)
            )

    print()
    return