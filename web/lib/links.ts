// The "+" menu. Fill in each href, and drop a square image at each icon path
// (web/public/links/...). An empty href renders the button but goes nowhere; a
// missing image shows a plain grey circle.
export type LinkItem = { label: string; href: string; icon: string };

export const LINKS: LinkItem[] = [
  { label: "GitHub Repo", href: "https://github.com/cyrusnavasca/cyrus-ai", icon: "/links/github.png" },
  { label: "My LinkedIn", href: "https://www.linkedin.com/in/cyrusnavasca/", icon: "/links/linkedin.webp" },
  { label: "My Portfolio", href: "https://cyrusnavasca.com", icon: "/links/portfolio.png" },
];
