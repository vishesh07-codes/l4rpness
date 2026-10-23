/**
 * Configure Monaco to load from the bundled `monaco-editor` package instead of
 * the default CDN, so the console works offline. Importing this module is a
 * side effect; the Files pane pulls it in, which keeps Monaco in the lazy
 * Campaign chunk rather than the initial bundle.
 */
import { loader } from "@monaco-editor/react"
import * as monaco from "monaco-editor"
import editorWorker from "monaco-editor/editor/editor.worker?worker"
import jsonWorker from "monaco-editor/language/json/json.worker?worker"
import cssWorker from "monaco-editor/language/css/css.worker?worker"
import htmlWorker from "monaco-editor/language/html/html.worker?worker"
import tsWorker from "monaco-editor/language/typescript/ts.worker?worker"

self.MonacoEnvironment = {
  getWorker(_workerId: string, label: string) {
    if (label === "json") return new jsonWorker()
    if (label === "css" || label === "scss" || label === "less") return new cssWorker()
    if (label === "html" || label === "handlebars" || label === "razor") return new htmlWorker()
    if (label === "typescript" || label === "javascript") return new tsWorker()
    return new editorWorker()
  },
}

loader.config({ monaco })

/**
 * Editor themes derived from the app's surface tokens (hex, since Monaco can't
 * read CSS variables), so the code pane blends with the surrounding panels
 * instead of sitting on `vs-dark`'s default #1e1e1e.
 */
monaco.editor.defineTheme("l4rpness-dark", {
  base: "vs-dark",
  inherit: true,
  rules: [],
  colors: {
    "editor.background": "#171717",
    "editor.foreground": "#fafafa",
    "editorCursor.foreground": "#fafafa",
    "editorLineNumber.foreground": "#929292",
    "editorLineNumber.activeForeground": "#a0a0a0",
    "editor.lineHighlightBackground": "#202020",
    "editor.lineHighlightBorder": "#00000000",
    "editorIndentGuide.background1": "#232323",
    "editorIndentGuide.activeBackground1": "#3a3a3a",
    "editorGutter.background": "#171717",
    "editorWidget.background": "#171717",
    "editorWidget.border": "#232323",
    "editor.selectionBackground": "#33333380",
    "editor.inactiveSelectionBackground": "#26262680",
    "editorOverviewRuler.border": "#00000000",
    "scrollbarSlider.background": "#ffffff14",
    "scrollbarSlider.hoverBackground": "#ffffff26",
    "scrollbarSlider.activeBackground": "#ffffff33",
  },
})

monaco.editor.defineTheme("l4rpness-light", {
  base: "vs",
  inherit: true,
  rules: [],
  colors: {
    "editor.background": "#ffffff",
    "editor.foreground": "#121212",
    "editorCursor.foreground": "#121212",
    "editorLineNumber.foreground": "#696969",
    "editorLineNumber.activeForeground": "#555555",
    "editor.lineHighlightBackground": "#f2f2f2",
    "editor.lineHighlightBorder": "#00000000",
    "editorIndentGuide.background1": "#e4e4e4",
    "editorIndentGuide.activeBackground1": "#c4c4c4",
    "editorGutter.background": "#ffffff",
    "editorWidget.background": "#ffffff",
    "editorWidget.border": "#e4e4e4",
    "editor.selectionBackground": "#d4d4d480",
    "editor.inactiveSelectionBackground": "#e4e4e480",
    "editorOverviewRuler.border": "#00000000",
    "scrollbarSlider.background": "#00000014",
    "scrollbarSlider.hoverBackground": "#00000026",
    "scrollbarSlider.activeBackground": "#00000033",
  },
})
