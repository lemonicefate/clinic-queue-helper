# Session-scoped queue order

Status: accepted

Each room maintains an independent local candidate order for each `TIME_KIND`. We choose session-scoped order over one shared room order because staff view separate clinic sessions and changes to one session must not reorder another. Local order organizes the display only; HIS remains responsible for actual calling.
