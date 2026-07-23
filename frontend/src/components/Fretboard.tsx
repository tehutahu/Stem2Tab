export interface FretboardNote {
  string: number;
  fret: number;
}

interface FretboardProps {
  activeNotes: FretboardNote[];
  strings?: number;
  frets?: number;
}

function Fretboard({ activeNotes, strings = 4, frets = 12 }: FretboardProps): JSX.Element {
  return (
    <div
      style={{
        display: "grid",
        gap: "4px",
        background: "#0f172a",
        padding: "12px",
        borderRadius: "10px",
      }}
    >
      {Array.from({ length: strings }, (_, stringIdx) => {
        const stringNumber = strings - stringIdx; // 1 = highest string in AlphaTab
        return (
          <div
            key={stringNumber}
            style={{
              display: "grid",
              gridTemplateColumns: `repeat(${frets + 1}, minmax(28px, 1fr))`,
              gap: "3px",
              alignItems: "center",
            }}
          >
            {Array.from({ length: frets + 1 }, (_, fretIdx) => {
              const isActive = activeNotes.some(
                (note) => note.string === stringNumber && note.fret === fretIdx,
              );
              return (
                <div
                  key={fretIdx}
                  style={{
                    height: "34px",
                    borderRadius: "6px",
                    border: "1px solid #1e293b",
                    background: isActive ? "linear-gradient(135deg, #22c55e, #16a34a)" : "#111827",
                    boxShadow: isActive ? "0 0 8px rgba(34,197,94,0.7)" : "inset 0 1px 0 #1f2937",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    color: isActive ? "#0b1726" : "#cbd5e1",
                    fontWeight: 600,
                    fontSize: "0.85rem",
                  }}
                >
                  {fretIdx}
                </div>
              );
            })}
          </div>
        );
      })}
    </div>
  );
}

export default Fretboard;


