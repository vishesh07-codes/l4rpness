// Icon barrel — Hugeicons behind lucide-familiar names.
//
// Components import from here, never from "@hugeicons/react" or
// the lucide package directly. Each export wraps a Hugeicons free glyph as a
// lucide-compatible component: it takes `className` (size with `size-*`,
// CSS width overrides the SVG attribute) and `strokeWidth` (default 1.5).
// See design.md §6.

import { HugeiconsIcon, type HugeiconsProps } from "@hugeicons/react"
import {
  Add01Icon,
  Alert02Icon,
  ArrowDown01Icon,
  ArrowDown02Icon,
  ArrowRight01Icon,
  ArrowLeft01Icon,
  ArrowReloadHorizontalIcon,
  ArrowUp01Icon,
  ArrowUp02Icon,
  Attachment01Icon,
  BookOpen01Icon,
  BracketsIcon,
  Cancel01Icon,
  CancelCircleIcon,
  CheckmarkCircle02Icon,
  CircleIcon,
  ComputerTerminal01Icon,
  Copy01Icon,
  CrosshairIcon,
  DashboardSquare01Icon,
  Delete02Icon,
  Doc01Icon,
  Edit02Icon,
  File01Icon,
  Folder01Icon,
  Globe02Icon,
  Image01Icon,
  InformationCircleIcon,
  LinkSquare02Icon,
  Loading03Icon,
  Menu01Icon,
  Message01Icon,
  Moon02Icon,
  MusicNote01Icon,
  PencilEdit02Icon,
  RefreshIcon,
  Search01Icon,
  Shield01Icon,
  SidebarLeft01Icon,
  SparklesIcon,
  SquareIcon,
  Sun03Icon,
  Table01Icon,
  TaskDaily01Icon,
  Tick02Icon,
  ThumbsDownIcon,
  ThumbsUpIcon,
  UnavailableIcon,
  Video01Icon,
  Wrench01Icon,
  Zip01Icon,
} from "@hugeicons/core-free-icons"

type IconProps = Omit<HugeiconsProps, "icon">
type Glyph = HugeiconsProps["icon"]

function icon(glyph: Glyph) {
  const Icon = (props: IconProps) => (
    <HugeiconsIcon icon={glyph as NonNullable<Glyph>} strokeWidth={1.5} {...props} />
  )
  return Icon
}

// names mirror lucide-react so call sites read the same
export const ArrowDown = icon(ArrowDown02Icon)
export const ArrowLeft = icon(ArrowLeft01Icon)
export const ArrowUp = icon(ArrowUp02Icon)
export const Ban = icon(UnavailableIcon)
export const BookOpenText = icon(BookOpen01Icon)
export const Braces = icon(BracketsIcon)
export const Check = icon(Tick02Icon)
// hugeicons' 01 variants are chevrons (no stem); 02 are true arrows
export const ChevronDown = icon(ArrowDown01Icon)
export const ChevronRight = icon(ArrowRight01Icon)
export const ChevronUp = icon(ArrowUp01Icon)
export const Circle = icon(CircleIcon)
export const CircleCheck = icon(CheckmarkCircle02Icon)
export const CircleX = icon(CancelCircleIcon)
export const Copy = icon(Copy01Icon)
export const Crosshair = icon(CrosshairIcon)
export const ExternalLink = icon(LinkSquare02Icon)
export const FileText = icon(File01Icon)
// file-type glyphs used by the attachment components
export const FileDoc = icon(Doc01Icon)
export const Folder = icon(Folder01Icon)
export const Sheet = icon(Table01Icon)
export const AudioFile = icon(MusicNote01Icon)
export const VideoFile = icon(Video01Icon)
export const ArchiveFile = icon(Zip01Icon)
export const Globe2 = icon(Globe02Icon)
export const ImageIcon = icon(Image01Icon)
export const Info = icon(InformationCircleIcon)
export const LayoutGrid = icon(DashboardSquare01Icon)
export const ListTodo = icon(TaskDaily01Icon)
export const Loader2 = icon(Loading03Icon)
export const LoaderCircle = icon(Loading03Icon)
export const Menu = icon(Menu01Icon)
export const MessageSquare = icon(Message01Icon)
export const Moon = icon(Moon02Icon)
export const Paperclip = icon(Attachment01Icon)
export const PencilLine = icon(Edit02Icon)
export const Plus = icon(Add01Icon)
export const RefreshCw = icon(RefreshIcon)
export const RotateCcw = icon(ArrowReloadHorizontalIcon)
export const Search = icon(Search01Icon)
export const Shield = icon(Shield01Icon)
export const SidebarToggle = icon(SidebarLeft01Icon)
export const Compose = icon(PencilEdit02Icon)
export const Sparkles = icon(SparklesIcon)
export const Square = icon(SquareIcon)
export const SquareTerminal = icon(ComputerTerminal01Icon)
export const Sun = icon(Sun03Icon)
export const ThumbsDown = icon(ThumbsDownIcon)
export const ThumbsUp = icon(ThumbsUpIcon)
export const Trash2 = icon(Delete02Icon)
export const TriangleAlert = icon(Alert02Icon)
export const Wrench = icon(Wrench01Icon)
export const X = icon(Cancel01Icon)
