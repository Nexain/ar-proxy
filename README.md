# ar-proxy

A local proxy for AgentRouter + GitHub Copilot Chat BYOK (Bring Your Own Key). This tool eliminates the need for manual patches to `extension.js`.

## Purpose

This proxy server provides two critical fixes for AgentRouter integration with GitHub Copilot Chat:

1. **Forces AgentRouter User-Agent**: Preserves the correct User-Agent header that Copilot would otherwise strip or normalize.
2. **Fixes malformed SSE chunks**: Drops malformed Server-Sent Events (SSE) chunks that crash the stream parser with "Cannot read properties of null (reading 'usage')" errors.

## Installation

### Node.js

```bash
npm install
```

### Python (no Node.js required)

The repository also includes a standard-library-only Python implementation, so no `pip install` is required.

```bash
python proxy.py
```

or:

```bash
python3 proxy.py
```

The Python implementation uses the same defaults as the Node.js proxy:

- Listen address: `127.0.0.1:8317`
- Upstream: `https://agentrouter.org`
- AgentRouter User-Agent: `claude-cli/0.0.0 (external, cli) (node/v20.0.0)`
- Malformed SSE frames are filtered before they reach Copilot

For debugging:

```bash
python proxy.py --verbose
python proxy.py --log
```

The Python implementation uses `read1()` for SSE responses so streamed tokens are forwarded as soon as they are available rather than waiting for a larger read buffer to fill.

## Usage

### Trae custom models

Run the proxy **on the same computer as Trae**, then add a separate custom
configuration for each model under **Settings → Models → Add Model → Custom
Model**. Enable **Full URL** and enter the matching API format and URL below.
Use your AgentRouter API key in each configuration.

| Model ID | Trae API format | Full request URL |
|----------|-----------------|------------------|
| `deepseek-v4-flash` | OpenAI Chat Completions | `http://127.0.0.1:8317/v1/chat/completions` |
| `gpt-6-astra` | OpenAI Chat Completions | `http://127.0.0.1:8317/v1/chat/completions` |
| `claude-opus-5` | Anthropic Messages | `http://127.0.0.1:8317/v1/messages` |
| `claude-opus-4-8` | Anthropic Messages | `http://127.0.0.1:8317/v1/messages` |

These mappings follow the protocol badges in the supplied AgentRouter model
list: `chat` means Chat Completions; `claude_native` means Anthropic Messages.
GPT in that list does **not** advertise `claude_native`, and those Claude models
do **not** advertise `chat`. Changing only the model ID in a Claude configuration
does not change its API protocol. The proxy forwards requests; it does not
convert Anthropic requests into OpenAI requests or vice versa.

Select the added custom model explicitly in Trae chat. For the first test, leave
sampling settings and context/output limits at their defaults. For GPT, use the
matching model-family setting if your Trae version exposes it: the official
documentation describes GPT-family adaptation of the output-token field.
Do not reuse Claude-specific sampling or output-limit settings for GPT.

The same settings are recorded in [trae-models.json](trae-models.json). This is a
reference file for manual configuration and the verification script, **not** a
Trae import format. Adjust the host and port if your proxy uses different values.
Keep `AR_UPSTREAM=https://agentrouter.org` when using these full URLs.

[Trae's official model configuration documentation](https://docs.trae.cn/ide_models)
explains the API formats and Full URL switch. Protocol badges indicate advertised
support, not guaranteed model availability: the screenshot marks GPT and both
Claude models as `Demote`.

Verify all four configurations from the computer running your proxy:

```bash
python verify_models.py
```

The script asks for the API key without displaying it, tests each model with
ordinary and streamed requests, and exits with an error if any test fails.
It uses short prompts and requests up to 256 output tokens so reasoning models
have some room to produce visible text. Eight inference requests may consume
API credit. You can target one model with `--model deepseek-v4-flash` or change
the endpoint with `--base-url http://127.0.0.1:8320`.

HTML/WAF responses now produce a JSON 502 error instead of being passed to Trae
as an apparently successful API response. An upstream authentication, quota,
model-availability, or unsupported-parameter error is passed through unchanged.
If tests fail, use the printed status and error to distinguish upstream problems
from Trae configuration. The proxy preserves the existing default User-Agent;
there is no verified basis for assigning different identities to these models.

The old `python ar_proxy.py` command is retained as an entry point to `proxy.py`,
so both Python commands use the streaming and response fixes.

### Quick Start

Run the proxy in the foreground:

```bash
npm start
```

Or use Node directly:

```bash
node proxy.js
```

Or use Python:

```bash
python proxy.py
```

### Windows (Minimized Window)

Run the included batch script to start the proxy in a minimized window:

```bash
start-proxy.cmd
```

## Configuration

Configure the proxy using environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `AR_PROXY_PORT` | `8317` | Port to listen on |
| `AR_PROXY_HOST` | `127.0.0.1` | Host to bind to |
| `AR_UPSTREAM` | `https://agentrouter.org` | Upstream AgentRouter URL |
| `AR_USER_AGENT` | `claude-cli/0.0.0 (external, cli) (node/v20.0.0)` | User-Agent header to send upstream |
| `AR_VERBOSE` | `0` | Set to `1` to enable verbose output (can also use `-v` or `--verbose` flag) |
| `AR_LOG` | `0` | Set to `1` to log full traffic (request/response status, headers, and bodies) |
| `AR_LOG_FILE` | (empty) | Write traffic logs to a file instead of stdout |
| `AR_LOG_BODY_LIMIT` | `65536` (64KB) | Maximum body size to log (in bytes) |

### Examples

**Start on port 8320 with verbose logging:**

```bash
AR_PROXY_PORT=8320 AR_VERBOSE=1 npm start
```

**Log all traffic to a file:**

```bash
AR_LOG_FILE=traffic.log npm start
```

**Custom upstream server:**

```bash
AR_UPSTREAM=https://custom-agentrouter.example.com npm start
```

**Windows batch with arguments:**

```bash
start-proxy.cmd --log --verbose
```

## How It Works

The proxy acts as a man-in-the-middle between Copilot Chat and AgentRouter:

1. Listens on the configured host and port (default: `127.0.0.1:8317`)
2. Accepts incoming requests from Copilot Chat
3. Forwards requests to the upstream AgentRouter server
4. Fixes the User-Agent header to match AgentRouter requirements
5. Filters malformed SSE chunks from responses
6. Streams the cleaned response back to the client

## Logging

### Verbose Mode

Shows basic request/response information:

```bash
node proxy.js --verbose
# or
AR_VERBOSE=1 npm start
```

### Full Traffic Logging

Logs complete request and response details (headers and bodies):

```bash
node proxy.js --log
# or
node proxy.js --log-file=traffic.log
```

Limit logged body size:

```bash
node proxy.js --log --log-body-limit=16384
```

## License

See LICENSE file for details.

## Support

For issues or questions, refer to the AgentRouter documentation or the project's issue tracker.
