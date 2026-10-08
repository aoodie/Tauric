export function WorkflowTabs({
  value,
  select,
  label,
  options,
}: {
  value: string;
  select: (value: string) => void;
  label: string;
  options: { id: string; label: string; count?: number }[];
}) {
  return (
    <div className="workflow-tabs" role="tablist" aria-label={label}>
      {options.map((option, index) => (
        <button
          key={option.id}
          id={`workflow-${option.id}`}
          type="button"
          role="tab"
          aria-selected={value === option.id}
          aria-controls={`workflow-panel-${option.id}`}
          tabIndex={value === option.id ? 0 : -1}
          onClick={() => select(option.id)}
          onKeyDown={(event) => {
            const next =
              event.key === "ArrowRight"
                ? (index + 1) % options.length
                : event.key === "ArrowLeft"
                  ? (index + options.length - 1) % options.length
                  : event.key === "Home"
                    ? 0
                    : event.key === "End"
                      ? options.length - 1
                      : -1;
            if (next < 0) return;
            event.preventDefault();
            select(options[next].id);
            document.getElementById(`workflow-${options[next].id}`)?.focus();
          }}
        >
          {option.label}
          {option.count !== undefined && <span>{option.count}</span>}
        </button>
      ))}
    </div>
  );
}
