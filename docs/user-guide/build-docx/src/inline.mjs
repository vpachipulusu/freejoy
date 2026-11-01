// Inline Markdown (bold, italic, code, links) -> docx runs and hyperlinks.
import { ExternalHyperlink, InternalHyperlink, TextRun } from 'docx';

function where(ctx) {
  return `${ctx.page}:${ctx.cursor.line}`;
}

/**
 * @param children markdown-it inline children
 * @param ctx      { page, cursor, links, bold? , plainLinks? }
 */
export function renderInline(children, ctx) {
  const root = [];
  const frames = [{ out: root, link: null }];
  let bold = ctx.bold ? 1 : 0;
  let italic = 0;
  let strike = 0;
  const top = () => frames[frames.length - 1];

  const run = (text, extra = {}) => {
    if (text === '') return;
    const inLink = frames.some((frame) => frame.link && frame.link.kind !== 'plain');
    top().out.push(
      new TextRun({
        text,
        bold: bold > 0 || undefined,
        italics: italic > 0 || undefined,
        strike: strike > 0 || undefined,
        style: extra.style ?? (inLink && !ctx.plainLinks ? 'Hyperlink' : undefined),
      }),
    );
  };

  for (const token of children) {
    switch (token.type) {
      case 'text':
        run(token.content);
        break;
      case 'code_inline':
        run(token.content, { style: 'CodeChar' });
        break;
      case 'softbreak':
        run(' ');
        break;
      case 'hardbreak':
        top().out.push(new TextRun({ break: 1 }));
        break;
      case 'strong_open':
        bold += 1;
        break;
      case 'strong_close':
        bold -= 1;
        break;
      case 'em_open':
        italic += 1;
        break;
      case 'em_close':
        italic -= 1;
        break;
      case 's_open':
        strike += 1;
        break;
      case 's_close':
        strike -= 1;
        break;
      case 'link_open': {
        const href = token.attrGet('href');
        frames.push({ out: [], link: ctx.links.resolve(href, ctx.page, where(ctx)) });
        break;
      }
      case 'link_close': {
        const frame = frames.pop();
        const { link } = frame;
        if (link.kind === 'internal') {
          top().out.push(new InternalHyperlink({ anchor: link.bookmark, children: frame.out }));
        } else if (link.kind === 'external') {
          top().out.push(new ExternalHyperlink({ link: link.url, children: frame.out }));
        } else {
          top().out.push(...frame.out);
        }
        break;
      }
      case 'image':
        throw new Error(`${where(ctx)}: an image inside a line of text is not supported; put it in its own paragraph`);
      default:
        throw new Error(`${where(ctx)}: unsupported inline Markdown "${token.type}"`);
    }
  }
  return root;
}
