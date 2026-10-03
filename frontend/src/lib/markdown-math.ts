/**
 * Pre-processes markdown text to safely handle dollar signs and math expressions:
 * 1. Protects block math ($$...$$) and code blocks (```...``` and `...`) from modification.
 * 2. Protects valid inline LaTeX math expressions ($...$) from having currency escaping applied.
 * 3. Escapes remaining standalone currency dollar signs (e.g. $50, $10.99) so they don't get misparsed
 *    by remark-math as math delimiters.
 */
export function preprocessMarkdownMath(content: string): string {
  if (!content) return content

  const placeholders: string[] = []
  const createPlaceholder = (val: string) => {
    const idx = placeholders.length
    placeholders.push(val)
    return `___MATH_PLACEHOLDER_${idx}___`
  }

  let text = content

  // 1. Protect fenced code blocks ```...```
  text = text.replace(/```[\s\S]*?```/g, (match) => createPlaceholder(match))

  // 2. Protect inline code `...`
  text = text.replace(/`[^`\n]+`/g, (match) => createPlaceholder(match))

  // 3. Protect explicit block math $$...$$
  text = text.replace(/\$\$[\s\S]*?\$\$/g, (match) => createPlaceholder(match))

  // 4. Protect existing escaped dollars \$
  text = text.replace(/\\\\\$/g, (match) => createPlaceholder(match))
  text = text.replace(/\\\$/g, (match) => createPlaceholder(match))

  // 5. Protect valid inline math $...$
  // Inline math starts with $ followed by non-whitespace and ends with non-whitespace followed by $
  text = text.replace(
    /(?<!\\)\$([^\s$](?:[\s\S]*?[^\s$])?)\$/g,
    (match, inner: string) => {
      // Check if this looks like a false match between two separate currency figures,
      // e.g. "$50 and Plan B costs $100" where inner is "50 and Plan B costs ".
      const isCurrencyPair =
        /^\d+(?:[.,]\d+)?\b.*?\b(?:and|to|or|vs\.?)\b.*?\b\d+(?:[.,]\d+)?\b/i.test(
          inner,
        ) ||
        (/^\d+(?:[.,]\d+)?\b/.test(inner) && /\$\d/.test(inner))

      if (isCurrencyPair) {
        return match // Do not protect; let step 6 escape currency
      }

      return createPlaceholder(match)
    },
  )

  // 6. Escape standalone currency dollar signs followed by digits (e.g. $50, $10.99, $1,500.50)
  text = text.replace(
    /(?<!\\)\$(\d+(?:[.,]\d+)?\b)/g,
    (_, amount) => `\\$${amount}`,
  )

  // 7. Restore all protected blocks in reverse order
  for (let i = placeholders.length - 1; i >= 0; i--) {
    text = text.replace(`___MATH_PLACEHOLDER_${i}___`, () => placeholders[i])
  }

  return text
}
