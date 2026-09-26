// A small native Harness plugin. All data access and validation stay in Python.
// No dependency on shell, filesystem tools or an external MCP service.
export const inject = ['tools'];

export async function apply(ctx) {
  const base = process.env.CHATLOCAL_BRIDGE_URL;
  const headers = { Authorization: `Bearer ${process.env.CHATLOCAL_BRIDGE_TOKEN}` };
  const response = await fetch(`${base}/tools`, { headers });
  if (!response.ok) throw new Error('Chat tool registration failed');
  for (const schema of await response.json()) {
    ctx.tools.register({
      ...schema,
      output: {
        schema: { type: 'string' },
        render: (_args, value) => [{ type: 'text', text: value }],
      },
      async execute(args, execution) {
        const result = await fetch(`${base}/tools/call`, {
          method: 'POST', signal: execution.signal,
          headers: { ...headers, 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: schema.name, arguments: args }),
        });
        if (!result.ok) throw new Error('Local chat query interrupted');
        return await result.text();
      },
    });
  }
}
