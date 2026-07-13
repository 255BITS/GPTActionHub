from quart import Quart, request
import together
import json
from pprint import pprint

app = Quart(__name__)

model_list = together.Models.list()

prompt_formats = {}
stop_tokens = {}

chat_templates = {
    'llama' : '<s>[INST] <<SYS>>\nAnswer the prompt\n<</SYS>>\n\n{prompt} [/INST]'
}

def update_prompt_formats():
    global model_list
    global prompt_format
    global chat_templates
    global stop_tokens

    for model in model_list:
        # newer together API responses use 'id' instead of 'name'
        name = model.get('name') or model.get('id')
        if name is None:
            continue
        if 'config' in model and 'prompt_format' in model['config']:
            prompt_formats[name]= model['config']['prompt_format']
        elif 'config' in model and 'chat_template_name' in model['config']:
            pprint(model)
            if model['config']['chat_template_name'] in chat_templates:
                prompt_formats[name] = chat_templates[model['config']['chat_template_name']]
        if 'config' in model and 'stop' in model['config']:
            stop_tokens[name]=model['config']['stop']
        else:
            stop_tokens[name]=["\n"]

update_prompt_formats()

@app.route("/model_list", methods=["GET"])
async def model_list():
    global model_list
    global prompt_format
    model_list = together.Models.list()
    update_prompt_formats()

    output = []
    for model in model_list:
        o = ""
        prompt_format = None
        if model['name'] in prompt_formats:
            prompt_format = prompt_formats[model['name']]
        o += "# "+model['display_name']
        o += "\nid: "+model['name']
        if 'context_length' in model:
            o += "\ncontext length: " + str(model['context_length'])
        o += "\nmodel type: "+model['display_type']
        if 'num_parameters' in model:
            o+="\nnum parameters(gigabytes): "+str(int(int(model["num_parameters"])/(1000**3)))
        o+="\ndescription: "+ model["description"].replace("\n", '\\n')
        o+="\n"
        output.append(o)
    return '\n'.join(output)

@app.route("/completion", methods=["POST"])
async def completion():
    api_key = os.environ.get("TOGETHER_API_KEY", None)
    if 'TOGETHER_API_KEY' in request.headers:
        api_key = request.headers["TOGETHER_API_KEY"]
    data = await request.get_data()
    data_dict = json.loads(data.decode('utf-8'))
    model_name = 'NousResearch/Nous-Hermes-Llama2-13b'
    if 'model_name' in data_dict:
        model_name = data_dict['model_name']
    prompt = data_dict['prompt']
    prompt_format = prompt_formats[model_name]
    full_prompt = prompt_format.replace("{prompt}", prompt)
    output = []
    output.append("Prompt: "+ prompt)
    response = together.Complete.create(
      prompt = full_prompt,
      model = model_name,
      max_tokens = 256,
      stop = stop_tokens[model_name]
    )
    output.append("Response: "+ response['output']['choices'][0]['text'])
    # print generated text
    return '\n'.join(output)

@app.route('/', methods=['GET'])
def ready():
    return "ready"

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=10010)
