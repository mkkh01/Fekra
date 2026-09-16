// الأنواع الأساسية لعالم اللعبة — لا منطق هنا، فقط تعريفات.

export type TerrainType = 'plains' | 'forest' | 'hills' | 'mountains' | 'desert' | 'water';

export interface Vec {
  x: number;
  y: number;
}

export interface City {
  name: string;
  population: number;
  isCapital: boolean;
  /** مستوى التحصين 0..3 */
  fortLevel: number;
}

export interface Province {
  id: number;
  name: string;
  /** خلايا الشبكة التابعة للمقاطعة (إحداثيات خلايا) */
  cells: Vec[];
  /** مركز المقاطعة (بوحدات الخلايا، قد يكون كسريًا) */
  center: Vec;
  terrain: TerrainType;
  /** معرفات المقاطعات المجاورة */
  neighbors: number[];
  /** معرف الدولة المالكة، -1 = بلا مالك (مياه) */
  ownerId: number;
  city: City | null;
  garrison: number;
  /** آخر نبضة حدثت فيها معركة هنا */
  lastBattleTick: number;
}

export interface Nation {
  id: number;
  name: string;
  color: string;
  darkColor: string;
  isPlayer: boolean;
  gold: number;
  food: number;
  capitalProvinceId: number;
  alive: boolean;
}

export type OrderKind = 'hold' | 'move' | 'retreat';
export type Stance = 'balanced' | 'aggressive' | 'defensive';
export type Formation = 'line' | 'column' | 'loose';

export interface ArmyOrder {
  kind: OrderKind;
  targetProvinceId: number | null;
}

export interface Army {
  id: number;
  nationId: number;
  name: string;
  commander: string;
  provinceId: number;
  soldiers: number;
  /** المعنويات 0..100 */
  morale: number;
  /** الإمداد 0..100 */
  supply: number;
  stance: Stance;
  formation: Formation;
  order: ArmyOrder;
  /** المسار المتبقي (معرفات مقاطعات) */
  path: number[];
  /** التقدم نحو المقاطعة التالية 0..1 */
  progress: number;
}

export type NewsKind = 'info' | 'war' | 'good' | 'bad';

export interface NewsItem {
  tick: number;
  text: string;
  kind: NewsKind;
}

/** إصدار صيغة الحفظ — يُرفع عند أي تغيير يكسر التوافق */
export const SAVE_VERSION = 1;
