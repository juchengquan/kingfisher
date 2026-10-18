"""Skills: registering what a workspace's `skills/` directory provides."""

#: The built-in a skill is opened with: the model is shown an index, and reads the skill it
#: picks. Here, not in `core.agent.middlewares.builtin_tools`, which imports it: the
#: definition readers refuse by this name, and importing that would load the agent runtime.
READ_FILE = "read_file"
