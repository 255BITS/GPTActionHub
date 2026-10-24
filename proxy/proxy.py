from quart import Quart, request, jsonify
from pprint import pprint
import httpx
from httpx import Timeout
import time
import asyncio
import logging
import traceback
import signal
import os
import json
import base64
import binascii
import secrets
from passlib.hash import bcrypt
from urllib.parse import quote, quote_plus
from datetime import datetime
import requests
import sys

app = Quart(__name__)
logging_tasks = set()
api_call_tasks = set()
url_api_lookup_table = {}
action_lookup_table = {}


# TODO use variables from db.py
# URL for fetching the mapping
DEFAULT_DATABASE = "gptactionhub"
DEFAULT_HOST = "localhost:5984"
DEFAULT_PASSWORD = "gptactionhub"
DEFAULT_PROTOCOL = "http://"
DEFAULT_USER = "gptactionhub"
COUCHDB_DATABASE = os.getenv("COUCHDB_DATABASE", DEFAULT_DATABASE)
COUCHDB_PASSWORD = os.getenv("COUCHDB_PASSWORD", DEFAULT_PASSWORD)
COUCHDB_HOST = os.getenv("COUCHDB_HOST", DEFAULT_HOST)
COUCHDB_USER = os.getenv("COUCHDB_USER", DEFAULT_USER)
COUCHDB_PROTOCOL = os.getenv("COUCHDB_PROTOCOL", DEFAULT_PROTOCOL)
COUCHDB_URL = COUCHDB_PROTOCOL+COUCHDB_USER+":"+COUCHDB_PASSWORD+"@"+COUCHDB_HOST+"/"+COUCHDB_DATABASE
COUCHDB_LOGS_URL = COUCHDB_URL+"-logs"
MAPPING_API_URL = COUCHDB_URL+"/_design/apis/_view/urls"
MAPPING_ACTION_URL = COUCHDB_URL+"/_design/actions/_view/api_links"
AUTH_MAPPING_URL = COUCHDB_URL+"/_design/auths/_view/by_actions?keys="
SLACK_WEBHOOK_URL = os.getenv('SLACK_WEBHOOK_URL', None)

# Setup asynchronous logging
logger = logging.getLogger("quart.app")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)
write_lock = asyncio.Lock()

couch_timeout = Timeout(120.0)
proxy_service_timeout = Timeout(180.0)

def terminate_and_send_slack_notification(message):
    payload = {"text": message}
    if SLACK_WEBHOOK_URL is not None:
        response = requests.post(SLACK_WEBHOOK_URL, json=payload)
    print(f"Failed to send Slack notification: {message}")
    sys.exit("Terminating program due to error.")


async def update_log_gpt_document(action_id, gpt_id):
    async with httpx.AsyncClient(timeout=couch_timeout) as client:
        try:
            # Fetch or create the log_gpt document for the given action_id
            response = await client.get(f"{COUCHDB_LOGS_URL}/log_gpt_{action_id}")
            if response.status_code == 200:
                doc = response.json()
            elif response.status_code == 404:
                doc = {'type': 'log_gpt', 'gpt_ids': [], 'action_id': action_id}
            else:
                logger.error(f"Failed to fetch log_gpt document: {response.text}")
                return

            # Add the new GPT ID to the set if it's not already present
            if gpt_id not in doc.get('gpt_ids', []):
                doc['gpt_ids'].append(gpt_id)
                update_response = await client.put(f"{COUCHDB_LOGS_URL}/log_gpt_{action_id}", json=doc)
                if update_response.status_code not in [201, 202]:
                    logger.error(f"Failed to update log_gpt document: {update_response.text}")
        except Exception as e:
            logger.error(f"Error in updating log_gpt document for action_id {action_id} with GPT ID {gpt_id}: {e}")

async def async_log(request_data, response_data, response_time, method, action_id, api_id, operation_id, path_id, response_status_code):
    # Prepare the document to be stored in CouchDB
    log_document = {
        "method": method,
        "action_id": action_id,
        "api_id": api_id,
        "operation_id": operation_id,
        "path_id": path_id,
        "request": request_data,
        "response": response_data,
        "response_status_code": response_status_code,
        "created_at": datetime.utcnow().isoformat() + "Z",  # UTC time in ISO 8601
        "type": "log",
        "response_time": response_time
    }

    # Log the data to CouchDB
    async with httpx.AsyncClient(timeout=couch_timeout) as client:
        try:
            # POST request to store the document in CouchDB
            response = await client.post(COUCHDB_LOGS_URL, json=log_document)
            if response.status_code != 201:
                # Handle the case where the document isn't created successfully
                logger.error(f"Failed to log to CouchDB: {response.text}")
        except Exception as e:
            logger.error(f"Error logging to CouchDB: {e}")
    await update_log_gpt_document(action_id, request_data["headers"].get("Openai-Gpt-Id"))

async def fetch_api_url_mapping():
    async with httpx.AsyncClient(timeout=couch_timeout) as client:
        try:
            response = await client.get(MAPPING_API_URL)
            if response.status_code != 200:
                terminate_and_send_slack_notification(f"Error fetching proxy apis on startup: {response.status_code}")
            return response.json()['rows']
        except httpx.RequestError as e:
            terminate_and_send_slack_notification(f"HTTP request error while fetching apis: {e}")

async def fetch_action_mapping():
    async with httpx.AsyncClient(timeout=couch_timeout) as client:
        try:
            response = await client.get(MAPPING_ACTION_URL)
        except httpx.RequestError as e:
            terminate_and_send_slack_notification(f"HTTP request error while fetching actions: {e}")
        if response.status_code != 200:
            terminate_and_send_slack_notification(f"Error fetching proxy actions on startup: {response.status_code}")
        actionrows = response.json()["rows"]
        authrows = await fetch_auths([row["id"] for row in actionrows])
        for actionrow in actionrows:
            actionrow['auths']=[]
            for authrow in authrows:
                if 'action_id' in authrow and authrow['action_id'] == actionrow['id']:
                    del authrow['action_id']
                    actionrow['auths'].append(authrow)
        return actionrows

async def fetch_auths(action_ids):
    async with httpx.AsyncClient(timeout=couch_timeout) as client:
        try:
            auths = await client.get(AUTH_MAPPING_URL+json.dumps([a_id for a_id in action_ids]))
            if auths.status_code != 200:
                terminate_and_send_slack_notification(f"Error fetching proxy auths on startup: {auth.status_code}")
            return [auth['value'] for auth in auths.json()['rows']]
        except httpx.RequestError as e:
            terminate_and_send_slack_notification(f"HTTP request error while fetching auths: {e}")

async def listen_to_changes(last_seq):
    """ Asynchronous version to listen for changes in the database """

    timeout_duration = 60  # Adjust this according to your needs
    timeout = Timeout(timeout_duration)
    async with httpx.AsyncClient() as client:
        while True:
            url = f"{COUCHDB_URL}//_changes?since={last_seq}&feed=longpoll&include_docs=true"
            try:
                response = await client.get(url)
                response.raise_for_status()
                changes = response.json()
                last_seq = changes['last_seq']

                async with write_lock:
                    for change in changes['results']:
                        if "deleted" in change and change["deleted"]:
                            if change["id"] in url_api_lookup_table:
                                del url_api_lookup_table[change["id"]]
                                print("Deleted api")
                                continue
                            if change["id"] in action_lookup_table:
                                del action_lookup_table[change["id"]]
                                print("Deleted action")
                                continue
                            print("Ignore deleted doc", change["id"])

                        doc = change.get('doc', {})
                        if doc.get('type') == 'API':
                            url_api_lookup_table[doc['_id']] = {'paths': doc['paths']}
                            logger.info("Updated API configuration")
                        elif doc.get('type') == 'actions':
                            action_lookup_table[doc['_id']] = {'api_links': doc['api_links'], 'auths': (await fetch_auths([doc["_id"]]))}
                            logger.info("Updated action configuration")
            except httpx.ReadTimeout:
                continue
            except httpx.HTTPStatusError as e:
                print(f"HTTP error occurred:", e)
            except Exception as e:
                print(f"An error occurred:", e)
                traceback.print_exc()

async def update_api_lookup_table():
    print("Updating api lookup table")
    api_urls = await fetch_api_url_mapping()
    print("APIs found", len(api_urls))
    async with write_lock:
        for a in api_urls:
            url_api_lookup_table[a['id']]=a['value']

async def update_action_lookup_table():
    print("Updating api lookup table")
    action_info = await fetch_action_mapping()
    print("Actions found", len(action_info))
    async with write_lock:
        for a in action_info:
            action_lookup_table[a['id']]={'api_links': a['value'], 'auths': a['auths']}

def validate_auth(header_token, auth):
    """Validate the Basic API credential against the action's stored hash."""
    try:
        if not isinstance(header_token, str) or len(header_token) > 2048:
            return False
        if not isinstance(auth, dict) or auth.get("auth_type") != "api_key_basic":
            return False
        decoded = base64.b64decode(header_token, validate=True).decode("utf-8")
        username, password = decoded.split(":", 1)
        if len(password.encode("utf-8")) > 72:
            return False
        expected_username = auth.get("username")
        password_hash = auth.get("password_hash")
        if not isinstance(expected_username, str) or not isinstance(password_hash, str):
            return False
        if not secrets.compare_digest(username.encode(), expected_username.encode()):
            return False
        return bcrypt.verify(password, password_hash)
    except (ValueError, TypeError, UnicodeError, binascii.Error):
        return False

@app.route('/', methods=['GET'])
def ready():
    return "ready"


def redact_log_value(value, credentials):
    """Remove known configured secrets from every request/response log field."""
    if isinstance(value, dict):
        return {redact_log_value(key, credentials): redact_log_value(item, credentials) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_log_value(item, credentials) for item in value]
    if isinstance(value, str):
        for credential in sorted(set(credentials), key=len, reverse=True):
            for representation in {credential, json.dumps(credential)[1:-1], quote(credential, safe=""), quote_plus(credential)}:
                if representation:
                    value = value.replace(representation, "[REDACTED]")
    return value


def get_value_from_source(param, headers):
    source = param["source"]
    if source == "constant":
        return param["value"]
    elif source == "conversation":
        return headers.get("Openai-Conversation-Id")
    elif source == "user":
        return headers.get("Openai-Ephemeral-User-Id")

@app.route('/<action_id>/<operation_id>', methods=['GET', 'POST', 'PUT', 'DELETE', "PATCH"])
async def passthrough(action_id, operation_id):
    start_time = time.time()
    method = request.method
    params = dict(request.args)
    data = await request.get_data()
    try:
        data_dict = json.loads(data.decode('utf-8'))
    except json.JSONDecodeError:
        data_dict = {}
    headers = dict(request.headers)
    if "ActionHub-Id" in headers:
        return jsonify({"error": "Proxy cannot call itself."}, 508)
    del headers["Host"]

    if action_id not in action_lookup_table:
        return jsonify({"error": "Action not found"}, 404)
    actions_api_links = action_lookup_table[action_id]["api_links"]
    api_link = None
    active_path_id = None
    active_action_path = None
    for a in actions_api_links:
        for path in a["paths"]:
            if path['operation_id']==operation_id:
                api_link = a
                active_path_id = path["path_id"]
                active_action_path = path
                break
    if api_link is None:
        return jsonify({"error": "API not found"}, 404)

    headers['ActionHub-Id'] = action_id

    if api_link['api_id'] not in url_api_lookup_table:
        return jsonify({"error": "API link found in action but not in API"}, 404)

    target = url_api_lookup_table[api_link['api_id']]
    target_path = None
    for path in target["paths"]:
        if path["path_id"] == active_path_id:
            target_path = path
            break

    auth_header = next((value for key, value in headers.items() if key.lower() == "authorization"), None)
    # Basic auth
    if auth_header and auth_header[:6].lower() == 'basic ':
        token = auth_header[6:]  # Strip off 'Basic ' prefix
        try:
            valid = False
            for auth in action_lookup_table[action_id]['auths']:
                if(validate_auth(token, auth)):
                    valid = True
                    break
            if(not valid):
                return jsonify({"error": 'Missing or invalid Authorization header (i)'}), 401
        except (TypeError, ValueError):
            return jsonify({"error": 'Missing or invalid Authorization header (e)'}), 401
    else:
        return jsonify({"error": 'Missing or invalid Authorization header'}), 401

    # This credential authenticates the proxy, not the upstream API.
    decoded_proxy_credential = base64.b64decode(auth_header[6:], validate=True).decode("utf-8")
    credentials = [auth_header, auth_header[6:], decoded_proxy_credential, decoded_proxy_credential.split(":", 1)[1]]
    for header_name in list(headers):
        if header_name.lower() == "authorization":
            del headers[header_name]
    api_url = target_path['url']
    for param in active_action_path["params"]:
        target_param = None
        for tp in target_path["params"]:
            if tp["name"] == param["name"]:
                target_param = tp
        if target_param is not None and target_param["type"] == "credential":
            credentials.append(str(param["value"]))
            if target_param['name'] == 'bearer':
                headers['Authorization'] = 'Bearer ' + param['value']
            elif target_param['name'] == 'basic':
                headers['Authorization'] = 'Basic ' + param['value']
            else:
                headers[target_param['name']] = param['value']
            continue

        api_url = api_url.replace(f"<{param['name']}>", str(param["value"]))

        value = get_value_from_source(param, headers)
        if value is not None:
            if target_path["method"].lower() in ["get", "delete"]:
                params[param["name"]] = value
            else:
                data_dict[param["name"]] = value

    method = request.method
    content = json.dumps(data_dict)
    content_length = str(len(content.encode('utf-8')))
    headers['Content-Length'] = content_length
    #TODO: openai token auth on action

    async with httpx.AsyncClient(timeout=proxy_service_timeout) as client:
        api_call = client.request(method=method, url=api_url, content=content, headers=headers, params=params)
        api_call_task = asyncio.create_task(api_call)
        api_call_tasks.add(api_call_task)
        api_call_task.add_done_callback(api_call_tasks.discard)

        try:
            response = await api_call_task
            response_data = response.text
        except httpx.RequestError as e:
            # dont await the log
            await async_log(data, "Upstream request failed", 0)
            return jsonify({"error": "Upstream request failed"}), 502

    response_time = time.time() - start_time

    sensitive_headers = {"authorization", "cookie", "set-cookie", "proxy-authorization"}
    sensitive_headers.update(p["name"].lower() for p in target_path["params"] if p.get("type") == "credential")
    logged_headers = {key: "[REDACTED]" if key.lower() in sensitive_headers else value for key, value in headers.items()}
    record_request = {
        "content": content,
        "params": params,
        "headers": logged_headers
    }
    record_response = {
        "content": response_data,
        "headers": {key: "[REDACTED]" if key.lower() in sensitive_headers else value for key, value in response.headers.items()}
    }
    record_request = redact_log_value(record_request, credentials)
    record_response = redact_log_value(record_response, credentials)
    do_log = async_log(record_request, record_response, response_time, method, action_id, api_link['api_id'], operation_id, active_path_id, response.status_code)
    log_task = asyncio.create_task(do_log)
    logging_tasks.add(log_task)
    log_task.add_done_callback(logging_tasks.discard)

    return jsonify(response_data), response.status_code

#TODO
async def shutdown():
    print("Shutting down active calls", 'api calls', len(api_call_tasks), 'logs', len(logging_tasks))
    all_tasks = logging_tasks.union(api_call_tasks)
    while len(all_tasks)>0:
        await asyncio.wait(all_tasks)

async def get_current_last_seq():
    """ Get the current last sequence number from the database """
    url = f"{COUCHDB_URL}/_changes?limit=1&descending=true"
    async with httpx.AsyncClient(timeout=couch_timeout) as client:
        response = await client.get(url)
        data = response.json()
        return data['last_seq']

listen_task = None

@app.before_serving
async def before_serving():
    #asyncio.get_event_loop().add_signal_handler(signal.SIGTERM, lambda: asyncio.create_task(shutdown()))
    #asyncio.get_event_loop().add_signal_handler(signal.SIGINT, lambda: asyncio.create_task(shutdown()))
    await update_api_lookup_table()  # Perform initial update of URL lookup table
    await update_action_lookup_table()  # Perform initial update of URL lookup table
    current_last_seq = await get_current_last_seq()
    listen_task = asyncio.create_task(listen_to_changes(current_last_seq))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=10004)

# TODO turn to test:
# curl -X POST "http://localhost:10004/6f78723e47c6be817762c0eaa20274c4/pressRedButtonPOST" \
#      -H "Remote-Addr: 127.0.0.1" \
#      -H "Host: 4d35-136-29-80-78.ngrok.io" \
#      -H "User-Agent: Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ChatGPT-User/1.0; +https://openai.com/bot" \
#      -H "Content-Length: 2" \
#      -H "Accept: */*" \
#      -H "Accept-Encoding: gzip, deflate" \
#      -H "Content-Type: application/json" \
#      -H "Openai-Conversation-Id: 4da183d3-b142-53a9-a9bf-30f163904ee6" \
#      -H "Openai-Ephemeral-User-Id: 7aaf0d0b-6b82-5a01-afec-3bcc4a3b9dfd" \
#      -H "Openai-Gpt-Id: g-Bk4k5UzvL" 

# Example document:
#{
#  "_id": "7a7760bae2aa09ef39bfc5740600430a",
#  "_rev": "2-9b70c0c3014b07d30ef3c42539430665",
#  "created_at": "2023-11-24T05:15:27.199556Z",
#  "updated_at": "2023-11-24T05:15:38.879305Z",
#  "api_links": [
#    {
#      "api_id": "6f78723e47c6be817762c0eaa201a12e",
#      "paths": [
#        {
#          "path_id": "d7eb4035-3ab6-4e88-b52b-f45a86548ded",
#          "operation_id": "pressRedButtonPOST",
#          "method": "POST",
#          "url": "http://localhost:10005/",
#          "params": [
#            {
#              "name": "pressure",
#              "source": "gpt",
#              "value": ""
#            }
#          ]
#        }
#      ]
#    }
#  ],
#  "name": "POST 2",
#  "type": "actions",
#  "user_id": "6f78723e47c6be817762c0eaa2007330"
#}
# running minimal-proxy
