#
# MIT License
#
# Copyright (c) 2020 Airbyte
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#


from functools import partial
from typing import Callable, Generator, List, Mapping, Optional, Tuple

from airbyte_protocol import AirbyteStream
from base_python import AirbyteLogger, BaseClient
from grnhse import Harvest
from grnhse.exceptions import HTTPError

DEFAULT_ITEMS_PER_PAGE = 100


def paginator(request, **params):
    """Split requests in multiple batches and return records as generator"""
    rows = request.get(**params)
    if "nested_names" in params:
        nested_names = params.pop("nested_names", None)
        if len(nested_names) > 1:
            params["nested_names"] = params["nested_names"][1:]
        for row in rows:
            yield from paginator(getattr(request(**params, object_id=row["id"]), nested_names[0]))
    else:
        yield from rows
    while request.records_remaining:
        rows = request.get_next()
        yield from rows


class HarvestClient(Harvest):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._uris["direct"] = {
            **self._uris["direct"],
            "demographics_question_sets": {"list": "demographics/question_sets", "retrieve": "demographics/question_sets/{id}"},
            "demographics_questions": {"list": "demographics/questions"},
            "demographics_answer_options": {"list": "demographics/answer_options", "retrieve": "demographics/answer_options/{id}"},
            "demographics_answers": {"list": "demographics/answers", "retrieve": "demographics/answers/{id}"},
        }
        self._uris["related"]["applications"] = {
            **self._uris["related"]["applications"],
            "demographics_answers": {"list": "applications/{rel_id}/demographics/answers"},
        }
        self._uris["related"] = {
            **self._uris["related"],
            "demographics_question_sets": {
                "questions": {
                    "list": "demographics/question_sets/{rel_id}/questions",
                    "retrieve": "demographics/question_sets/{rel_id}/questions/{{id}}",
                }
            },
            "demographics_answers": {"answer_options": {"list": "demographics/questions/{rel_id}/answer_options"}},
        }


class Client(BaseClient):
    ENTITIES = [
        "applications",
        "candidates",
        "close_reasons",
        "degrees",
        "departments",
        "job_posts",
        "jobs",
        "offers",
        "scorecards",
        "users",
        "custom_fields",
        "demographics_question_sets",
        "demographics_questions",
        "demographics_answer_options",
        "demographics_answers",
        "applications.demographics_answers",
        "demographics_question_sets.questions",
        "demographics_answers.answer_options",
        "interviews",
        "applications.interviews",
        "sources",
        "rejection_reasons",
        "jobs.openings",
        "job_stages",
        "jobs.stages",
    ]

    def __init__(self, api_key):
        self._client = HarvestClient(api_key=api_key)
        super().__init__()

    def list(self, name, **kwargs):
        name_parts = name.split(".")
        nested_names = name_parts[1:]
        kwargs["per_page"] = DEFAULT_ITEMS_PER_PAGE
        if nested_names:
            kwargs["nested_names"] = nested_names
        yield from paginator(getattr(self._client, name_parts[0]), **kwargs)

    def _enumerate_methods(self) -> Mapping[str, Callable]:
        return {entity: partial(self.list, name=entity) for entity in self.ENTITIES}

    def get_accessible_endpoints(self) -> List[str]:
        """Try to read each supported endpoint and return accessible stream names"""
        logger = AirbyteLogger()
        accessible_endpoints = []
        for entity in self.ENTITIES:
            try:
                getattr(self._client, entity).get()
                accessible_endpoints.append(entity)
            except HTTPError as error:
                logger.warn(f"Endpoint '{entity}' error: {str(error)}")
                if "This API Key does not have permission for this endpoint" not in str(error):
                    raise error
        logger.info(f"API key has access to {len(accessible_endpoints)} endpoints: {accessible_endpoints}")
        return accessible_endpoints

    def health_check(self) -> Tuple[bool, Optional[str]]:
        alive = True
        error_msg = None
        try:
            accessible_endpoints = self.get_accessible_endpoints()
            if not accessible_endpoints:
                alive = False
                error_msg = "Your API Key does not have permission for any existing endpoints. Please grant read permissions for required streams/endpoints"

        except HTTPError as error:
            alive = False
            error_msg = str(error)

        return alive, error_msg

    @property
    def streams(self) -> Generator[AirbyteStream, None, None]:
        """Process accessible streams only"""
        accessible_endpoints = self.get_accessible_endpoints()
        for stream in super().streams:
            if stream.name in accessible_endpoints:
                yield stream
