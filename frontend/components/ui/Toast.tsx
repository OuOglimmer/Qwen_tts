export function Toast({ notice }: { notice: string }) {
  if (!notice) return null
  return <div className="toast">{notice}</div>
}
