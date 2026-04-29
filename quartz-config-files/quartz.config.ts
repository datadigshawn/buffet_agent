import { QuartzConfig } from "./quartz/cfg"
import * as Plugin from "./quartz/plugins"

/**
 * 巴菲特股東信知識庫 — Quartz 4 設定
 *
 * 這份設定針對中文知識庫優化:
 * - 採用思源宋體(中文)+ JetBrains Mono(代碼)
 * - 啟用 Graph view、雙向連結、Backlinks
 * - SPA 路由優化中文 anchor
 * - 主題色為「巴菲特紙紅」+「奧馬哈白」
 */

const config: QuartzConfig = {
  configuration: {
    pageTitle: "📚 巴菲特股東信知識庫",
    pageTitleSuffix: " | Buffett KB",
    enableSPA: true,
    enablePopovers: true,
    analytics: {
      provider: "plausible",
    },
    locale: "zh-TW",
    baseUrl: "buffett-kb.example.com",  // ← 部署後改成你自己的網址
    ignorePatterns: ["private", "templates", ".obsidian", "05-模板"],
    defaultDateType: "created",
    theme: {
      fontOrigin: "googleFonts",
      cdnCaching: true,
      typography: {
        header: "Noto Serif TC",
        body: "Noto Sans TC",
        code: "JetBrains Mono",
      },
      colors: {
        lightMode: {
          light: "#faf8f5",          // 米白(像舊書頁)
          lightgray: "#e5e0d4",
          gray: "#a09787",
          darkgray: "#4a4540",
          dark: "#2a2521",
          secondary: "#8b3a2f",      // 巴菲特紙紅(蘋果商標的紅)
          tertiary: "#b87333",       // 奧馬哈赭色
          highlight: "rgba(184, 115, 51, 0.15)",
          textHighlight: "rgba(184, 115, 51, 0.35)",
        },
        darkMode: {
          light: "#1a1816",
          lightgray: "#3a352e",
          gray: "#7a7268",
          darkgray: "#d4cdbe",
          dark: "#ebe6db",
          secondary: "#d4815a",
          tertiary: "#e0a070",
          highlight: "rgba(212, 129, 90, 0.18)",
          textHighlight: "rgba(212, 129, 90, 0.40)",
        },
      },
    },
  },
  plugins: {
    transformers: [
      Plugin.FrontMatter(),
      Plugin.CreatedModifiedDate({
        priority: ["frontmatter", "filesystem"],
      }),
      Plugin.SyntaxHighlighting({
        theme: { light: "github-light", dark: "github-dark" },
        keepBackground: false,
      }),
      Plugin.ObsidianFlavoredMarkdown({ enableInHtmlEmbed: false }),
      Plugin.GitHubFlavoredMarkdown(),
      Plugin.TableOfContents(),
      Plugin.CrawlLinks({ markdownLinkResolution: "shortest" }),
      Plugin.Description(),
      Plugin.Latex({ renderEngine: "katex" }),
    ],
    filters: [
      Plugin.RemoveDrafts(),
      // Plugin.ExplicitPublish(),  // 若想只發佈 frontmatter 標 publish: true 的,啟用此行
    ],
    emitters: [
      Plugin.AliasRedirects(),
      Plugin.ComponentResources(),
      Plugin.ContentPage(),
      Plugin.FolderPage(),
      Plugin.TagPage(),
      Plugin.ContentIndex({
        enableSiteMap: true,
        enableRSS: true,
      }),
      Plugin.Assets(),
      Plugin.Static(),
      Plugin.NotFoundPage(),
    ],
  },
}

export default config
