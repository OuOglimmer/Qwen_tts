export function SectionTitle({ number, title, description }: { number?: string; title: string; description: string }) {
  return <div className="doc-title"><h1>{number ? `${number} · ` : ''}{title}</h1><p>{description}</p></div>
}
