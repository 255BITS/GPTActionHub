import couchdb
import traceback
from typing import List, Union
from shared import utils

class DB:
    def __init__(self, couch_credentials, partition=None):
        self.server = couchdb.Server(url=couch_credentials['url'])
        self.db_name = couch_credentials['db_name']
        self.partition = partition

        # Create the database if it doesn't exist
        if self.db_name not in self.server:
            self.server.create(self.db_name)
        self.db = self.server[self.db_name]
        self.create_views()

    def create_views(self):
        if self.partition == "app":
            self.create_actions_views()
            self.create_api_views()
            self.create_auth_views()
            self.create_user_views()
        else:
            self.create_log_views()

    def insert(self, data):
        try:
            doc_id, doc_rev = self.db.save(data)
            return doc_id, doc_rev
        except couchdb.http.ResourceConflict:
            # Handle conflict if necessary
            return None

    def create_view_ddoc(self, design_doc_name, view_name, map_func, reduce_func=None):
        """
        Utility function to create a view design document and check for its existence.
        Optionally, a reduce function can be added.
        """
        design_doc_id = f"_design/{design_doc_name}"

        # Initialize the design document if it doesn't exist
        if design_doc_id not in self.db:
            self.db[design_doc_id] = {
                "_id": design_doc_id,
                "views": {}
            }

        current_design_doc = self.db[design_doc_id]
        update_needed = False  # Flag to determine if an update is needed

        # Check if the view already exists within the design document
        if view_name not in current_design_doc["views"]:
            current_design_doc["views"][view_name] = {
                "map": map_func
            }
            if reduce_func:
                current_design_doc["views"][view_name]["reduce"] = reduce_func
            update_needed = True  # New view, so an update is needed
        else:
            # Check if the map function has changed
            existing_map_func = current_design_doc["views"][view_name].get("map", "")
            existing_reduce_func = current_design_doc["views"][view_name].get("reduce", "")

            if existing_map_func != map_func:
                current_design_doc["views"][view_name]["map"] = map_func
                update_needed = True  # Map function changed, so an update is needed

            if existing_reduce_func != reduce_func:
                if reduce_func:
                    current_design_doc["views"][view_name]["reduce"] = reduce_func
                else:
                    # Remove the reduce key if reduce_func is None
                    current_design_doc["views"][view_name].pop("reduce", None)
                update_needed = True  # Reduce function changed, so an update is needed

        # Update the design document in the database if needed
        if update_needed:
            try:
                self.db[design_doc_id] = current_design_doc
            except couchdb.http.ResourceConflict:
                # Another worker is updating the view
                pass

    def create_actions_views(self):
        """
        Create views related to actions.
        """
        by_user = """function(doc) {
                                if (doc.type === 'actions') {
                                    emit(doc.user_id, doc);
                                }
                            }"""

        api_links = """function(doc) {
                                if (doc.type === 'actions') {
                                    emit(doc._id, doc.api_links);
                                }
                            }"""

        count_api_links = """function(doc) {
                                if (doc.type === 'actions') {
                                    doc.api_links.forEach(api_link => emit(api_link.api_id, 1))
                                }
                            }"""


        self.create_view_ddoc("actions", "by_user", by_user, reduce_func="_count")
        self.create_view_ddoc("actions", "api_links", api_links)
        self.create_view_ddoc("actions", "count_api_links", count_api_links, reduce_func="_sum")

    def create_auth_views(self):
        """
        Create views related to api keys.
        """
        by_actions = """function(doc) {
                                if (doc.type === 'auth') {
                                    emit(doc.action_id, doc);
                                }
                            }"""

        self.create_view_ddoc("auths", "by_actions", by_actions)

    def create_api_views(self):
        """
        Create views related to apis.
        """
        map_count = """function(doc) { 
                                if (doc.type === 'API') { 
                                    emit(null, null); 
                                } 
                            }"""
        public = """function(doc) { 
                                if (doc.type === 'API' && doc.visibility === 'public') { 
                                    emit(null, doc); 
                                } 
                            }"""
        public_or_by_user = """
            function (doc) {
              if(doc.type === "API") {
                 if (doc.visibility === 'public') {
                  emit(['public', 0], doc);
                } else if (doc.user_id) {
                  // Emit for user-specific documents
                  emit([doc.user_id, 1], doc);
                }
              }
            }
        """

        by_user = """function(doc) { 
                                if (doc.type === 'API') { 
                                    emit(doc.user_id, doc); 
                                } 
                            }"""

        urls = """function(doc) { 
                                if (doc.type === 'API') { 
                                    emit(null, {params: doc.params, paths: doc.paths}); 
                                } 
                            }"""

        self.create_view_ddoc("apis", "count", map_count, reduce_func="_count")
        self.create_view_ddoc("apis", "public", public)
        self.create_view_ddoc("apis", "public_or_by_user", public_or_by_user, reduce_func="_count")
        self.create_view_ddoc("apis", "by_user", by_user)
        self.create_view_ddoc("apis", "urls", urls)

    def create_user_views(self):
        map_users_by_email = """function(doc) {
                                if (doc.type === 'user' && doc.email) {
                                    emit(doc.email, doc);
                                } 
                            }"""
        self.create_view_ddoc("users", "by_email", map_users_by_email)

    def create_log_views(self):
        by_api = """function(doc) {
                                if (doc.type === 'log') {
                                    emit(doc.api_id, doc);
                                }
                            }"""
        by_actions = """function(doc) {
                                if (doc.type === 'log') {
                                    emit(doc.action_id, doc);
                                }
                            }"""
        map_count_by_actions = """function(doc) { 
                                if (doc.type === 'log') { 
                                    emit(doc.action_id, 1); 
                                } 
                            }"""
        map_count_by_api = """function(doc) { 
                                if (doc.type === 'log') { 
                                    emit(doc.api_id, 1); 
                                } 
                            }"""
        map_last_used = """function(doc) {
                               if (doc.type === 'log' && doc.action_id && doc.created_at) {
                                   emit(doc.api_id, doc.created_at);
                               }
                           }
                           """

        reduce_last_used = """
                            function(keys, values, rereduce) {
                                return values.reduce(function(a, b) {
                                    return a > b ? a : b;
                                }, 0);
                            }
                            """

        map_count_gpt_ids_by_action = """
            function(doc) {
                if (doc.type === 'log_gpt') {
                    emit(doc.action_id, doc.gpt_ids.length);
                }
            }
        """
        map_by_actions_day = """
            function(doc) {
                if (doc.type === "log") {
                    var date = new Date(doc.created_at);
                    var key = [doc.action_id, date.getFullYear(), date.getMonth() + 1, date.getDate()];
                    emit(key, 1);
                }
            }
        """

        self.create_view_ddoc("logs", "count_gpt_ids_by_action", map_count_gpt_ids_by_action)
        self.create_view_ddoc("logs", "count_by_actions", map_count_by_actions, reduce_func="_sum")
        self.create_view_ddoc("logs", "count_by_api", map_count_by_api, reduce_func="_sum")
        self.create_view_ddoc("logs", "by_api", by_api, reduce_func="_count")
        self.create_view_ddoc("logs", "by_actions", by_actions, reduce_func="_count")
        self.create_view_ddoc("logs", "apis_last_used", map_last_used, reduce_last_used)
        self.create_view_ddoc("logs", "count_by_actions_day", map_by_actions_day, reduce_func="_sum")

    def query_view(self, design_doc, view_name, **kwargs) -> List:
        """
        Query a CouchDB view and return the results as a list.
        """
        view_result = self.db.view(f'{design_doc}/{view_name}', **kwargs)
        return [row.value for row in view_result] if view_result else []

    def count_apis(self):
        result = self.db.view('apis/count', reduce=True)
        count = result.rows[0]['value'] if result.rows else 0
        return count

    def delete(self, doc_id):
        del self.db[doc_id]

    def save(self, data):
        try:
            current_datetime = utils.get_current_datetime_iso8601()
            defaults = {
                "created_at": current_datetime,
                "updated_at": current_datetime
            }
            if "updated_at" in data:
                data["updated_at"] = current_datetime
            doc_id, doc_rev = self.db.save(defaults | data)
            return self.db[doc_id]
        except couchdb.http.ResourceConflict:
            traceback.print_exc()
            # Handle conflict if necessary
            return None

    def get(self, ids):
        is_single_id = isinstance(ids, str)
        keys = [ids] if is_single_id else ids
        documents = [row.doc for row in self.db.view('_all_docs', keys=keys, include_docs=True)]
        return documents[0] if is_single_id and documents else documents

    def get_user_by_email(self, email):
        """
        Get a user by email
        """
        users = self.query_view('users', 'by_email', key=email)
        return users[0] if users else None

    def get_actions_for_user(self, user_id):
        return self.query_view('actions', 'by_user', key=user_id)

    def get_auths_for_actions(self, action_id):
        return self.query_view('auths', 'by_actions', key=action_id)

    def get_apis(self, action_id):
        return self.query_view('auths', 'by_actions', key=action_id)

    def count_logs_by_actions(self, keys=None):
        view_path = 'logs/count_by_actions'
        query_params = {'reduce': True}
        if keys is not None:
            query_params['keys'] = keys
        query_params['group'] = True

        result = self.db.view(view_path, **query_params)
        counts = {row.key: row.value for row in result.rows} if result.rows else {}
        return counts

    def count_logs_by_api(self, keys=None):
        view_path = 'logs/count_by_api'
        query_params = {'reduce': True}
        if keys is not None:
            query_params['keys'] = keys
        query_params['group'] = True

        result = self.db.view(view_path, **query_params)
        counts = {row.key: row.value for row in result.rows} if result.rows else {}
        return counts

    def count_api_links(self, keys):
        result = self.db.view('actions/count_api_links', reduce=True, keys=keys, group=True)
        counts = {row.key: row.value for row in result.rows} if result.rows else {}
        return counts

    def count_gpt_ids_by_actions(self, keys=None):
        result = self.db.view('logs/count_gpt_ids_by_action', keys=keys)
        return {row.key: row.value for row in result.rows}

    def get_apis_last_used(self, keys):
        # Define the view path
        view_path = 'logs/apis_last_used'

        # Query the view with the specified keys
        result = self.db.view(view_path, keys=keys, reduce=True, group=True)

        # Initialize an empty dictionary to store the results
        last_used_timestamps = {}

        # Iterate over the returned rows
        for row in result.rows:
            # Map each action_id to its latest timestamp
            last_used_timestamps[row.key] = row.value

        # Return the dictionary containing action_id: latest_timestamp pairs
        return last_used_timestamps

    def count_logs_by_actions_day(self, action_id, start_date, end_date):
        # Format start and end keys for the view query
        start_key = [action_id, start_date.year, start_date.month, start_date.day]
        end_key = [action_id, end_date.year, end_date.month, end_date.day]

        result = self.db.view(
            'logs/count_by_actions_day',
            reduce=True,
            startkey=start_key,
            endkey=end_key,
            group=True
        )

        counts = {}
        for row in result.rows:
            key = (row.key[0], row.key[1], row.key[2], row.key[3])
            counts[key] = row.value

        return counts

