import { defineConfig } from "astro/config";

// GitHub Pages 프로젝트 사이트: https://boboseong.github.io/AgeShelf/
// 경로 접두사는 src/config.ts 의 SITE_BASE 와 같게 유지한다.
export default defineConfig({
  site: "https://boboseong.github.io",
  base: "/AgeShelf",
  trailingSlash: "ignore",
});
