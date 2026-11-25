// board.h: the Waveshare ESP32-S3-LCD-2.8C's round 480x480 screen, set up the way face.ino will:
// two frame buffers in PSRAM that swap at vsync, and bounce buffers in internal RAM.
//
// The screen is an ST7701 driver chip. It takes its start-up commands over 3-wire SPI (its chip
// select and reset sit on a TCA9554 IO expander), then shows whatever the ESP32-S3's 16-bit RGB
// interface sends it, 480x480 RGB565, over and over. Pins, timings and the start-up commands are
// from Waveshare's demo (ESP32-S3-LCD-2.8C-Demo.zip, LVGL_Arduino/Display_ST7701.cpp).
//
// Also here: a helper task on core 0. The screen's interrupts (and with them the bounce-buffer
// copying) run on the core that created the panel, so core 0 creates it and also queues each
// finished frame (see queueFrame). Core 1 (Arduino's loop) is left to draw.
#pragma once
#include <Arduino.h>
#include <Wire.h>
#include "driver/spi_master.h"
#include "esp_cache.h"
#include "esp_heap_caps.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_rgb.h"

#define LCD_W 480
#define LCD_H 480

// --- pins ----------------------------------------------------------------------------------------
static const int PIN_I2C_SDA = 15, PIN_I2C_SCL = 7;       // the IO expander (and touch, RTC, IMU)
static const int PIN_LCD_SDA = 1, PIN_LCD_SCL = 2;        // ST7701 set-up (3-wire SPI)
static const int PIN_BACKLIGHT = 6;
static const int PIN_HSYNC = 38, PIN_VSYNC = 39, PIN_DE = 40, PIN_PCLK = 41;
// RGB565 data lines, bit 0 to bit 15: blue B1-B5, green G0-G5, red R1-R5
static const int PIN_DATA[16] = {5, 45, 48, 47, 21, 14, 13, 12, 11, 10, 9, 46, 3, 8, 18, 17};

// RGB timings from Waveshare's demo. Each line is 480 pixels plus 68 clocks of sync and porches,
// each frame 480 lines plus 28, so the panel refreshes pclk / (548 x 508) times a second:
// 36 Hz at 10 MHz, 43 Hz at 12, 57 Hz at 16, 72 Hz at 20, 108 Hz at 30.
static const int H_PULSE = 8, H_BACK = 10, H_FRONT = 50;
static const int V_PULSE = 2, V_BACK = 18, V_FRONT = 8;

static float refreshHz(float pclkMHz) {
  return pclkMHz * 1e6f / ((LCD_W + H_PULSE + H_BACK + H_FRONT) * (LCD_H + V_PULSE + V_BACK + V_FRONT));
}

// --- TCA9554 IO expander -------------------------------------------------------------------------
static const uint8_t EXPANDER_ADDR = 0x20;
static const uint8_t EXIO_LCD_RST = 1 << 0;   // EXIO1
static const uint8_t EXIO_TP_RST = 1 << 1;    // EXIO2: touch reset
static const uint8_t EXIO_LCD_CS = 1 << 2;    // EXIO3
static const uint8_t EXIO_SD_CS = 1 << 3;     // EXIO4
static const uint8_t EXIO_BUZZER = 1 << 7;    // EXIO8
static uint8_t expanderOut;

static bool expanderWrite(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(EXPANDER_ADDR);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

static void expanderSet(uint8_t bits, bool high) {
  expanderOut = high ? (expanderOut | bits) : (expanderOut & ~bits);
  expanderWrite(0x01, expanderOut);
}

// --- ST7701 start-up commands --------------------------------------------------------------------
// Each entry: command, number of data bytes, the bytes. DELAY_120 instead of a count means "no data,
// then wait 120 ms".
static const uint8_t DELAY_120 = 0xFF;
static const uint8_t ST7701_INIT[] = {
  0xFF, 5, 0x77, 0x01, 0x00, 0x00, 0x13,
  0xEF, 1, 0x08,
  0xFF, 5, 0x77, 0x01, 0x00, 0x00, 0x10,
  0xC0, 2, 0x3B, 0x00,
  0xC1, 2, 0x10, 0x0C,
  0xC2, 2, 0x07, 0x0A,
  0xC7, 1, 0x00,
  0xCC, 1, 0x10,
  0xCD, 1, 0x08,
  0xB0, 16, 0x05, 0x12, 0x98, 0x0E, 0x0F, 0x07, 0x07, 0x09, 0x09, 0x23, 0x05, 0x52, 0x0F, 0x67, 0x2C, 0x11,
  0xB1, 16, 0x0B, 0x11, 0x97, 0x0C, 0x12, 0x06, 0x06, 0x08, 0x08, 0x22, 0x03, 0x51, 0x11, 0x66, 0x2B, 0x0F,
  0xFF, 5, 0x77, 0x01, 0x00, 0x00, 0x11,
  0xB0, 1, 0x5D,
  0xB1, 1, 0x3E,
  0xB2, 1, 0x81,
  0xB3, 1, 0x80,
  0xB5, 1, 0x4E,
  0xB7, 1, 0x85,
  0xB8, 1, 0x20,
  0xC1, 1, 0x78,
  0xC2, 1, 0x78,
  0xD0, 1, 0x88,
  0xE0, 3, 0x00, 0x00, 0x02,
  0xE1, 11, 0x06, 0x30, 0x08, 0x30, 0x05, 0x30, 0x07, 0x30, 0x00, 0x33, 0x33,
  0xE2, 12, 0x11, 0x11, 0x33, 0x33, 0xF4, 0x00, 0x00, 0x00, 0xF4, 0x00, 0x00, 0x00,
  0xE3, 4, 0x00, 0x00, 0x11, 0x11,
  0xE4, 2, 0x44, 0x44,
  0xE5, 16, 0x0D, 0xF5, 0x30, 0xF0, 0x0F, 0xF7, 0x30, 0xF0, 0x09, 0xF1, 0x30, 0xF0, 0x0B, 0xF3, 0x30, 0xF0,
  0xE6, 4, 0x00, 0x00, 0x11, 0x11,
  0xE7, 2, 0x44, 0x44,
  0xE8, 16, 0x0C, 0xF4, 0x30, 0xF0, 0x0E, 0xF6, 0x30, 0xF0, 0x08, 0xF0, 0x30, 0xF0, 0x0A, 0xF2, 0x30, 0xF0,
  0xE9, 2, 0x36, 0x01,
  0xEB, 7, 0x00, 0x01, 0xE4, 0xE4, 0x44, 0x88, 0x40,
  0xED, 16, 0xFF, 0x10, 0xAF, 0x76, 0x54, 0x2B, 0xCF, 0xFF, 0xFF, 0xFC, 0xB2, 0x45, 0x67, 0xFA, 0x01, 0xFF,
  0xEF, 6, 0x08, 0x08, 0x08, 0x45, 0x3F, 0x54,
  0xFF, 5, 0x77, 0x01, 0x00, 0x00, 0x00,
  0x11, DELAY_120,           // sleep out
  0x3A, 1, 0x66,             // pixel format
  0x36, 1, 0x00,             // scan direction
  0x35, 1, 0x00,             // tearing-effect line on
  0x29, 0,                   // display on
};

static spi_device_handle_t lcdSpi;

// One 9-bit word: a data/command bit, then the byte.
static void st7701Send(bool data, uint8_t value) {
  spi_transaction_t t;
  memset(&t, 0, sizeof t);
  t.cmd = data ? 1 : 0;
  t.addr = value;
  spi_device_transmit(lcdSpi, &t);
}

// Wakes the expander, resets the ST7701 and sends it its start-up commands. False if the expander
// doesn't answer (wrong board, or a loose I2C bus).
static bool st7701Init() {
  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL, 400000);
  // Outputs: LCD reset, touch reset, LCD CS, SD CS high; the buzzer off. EXIO5-7 are interrupt
  // lines from the IMU and RTC, so they stay inputs.
  expanderOut = EXIO_LCD_RST | EXIO_TP_RST | EXIO_LCD_CS | EXIO_SD_CS;
  if (!expanderWrite(0x01, expanderOut) || !expanderWrite(0x03, 0x70)) return false;
  expanderSet(EXIO_BUZZER, false);
  expanderSet(EXIO_LCD_RST, false);
  delay(10);
  expanderSet(EXIO_LCD_RST, true);
  delay(50);

  spi_bus_config_t bus;
  memset(&bus, 0, sizeof bus);
  bus.mosi_io_num = PIN_LCD_SDA;
  bus.miso_io_num = -1;
  bus.sclk_io_num = PIN_LCD_SCL;
  bus.quadwp_io_num = -1;
  bus.quadhd_io_num = -1;
  bus.data4_io_num = bus.data5_io_num = bus.data6_io_num = bus.data7_io_num = -1;
  bus.max_transfer_sz = 64;
  if (spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_DISABLED) != ESP_OK) return false;
  spi_device_interface_config_t dev;
  memset(&dev, 0, sizeof dev);
  dev.command_bits = 1;       // the data/command bit
  dev.address_bits = 8;       // the byte
  dev.mode = 0;
  dev.clock_speed_hz = 10 * 1000 * 1000;
  dev.spics_io_num = -1;      // chip select is on the expander
  dev.queue_size = 1;
  if (spi_bus_add_device(SPI2_HOST, &dev, &lcdSpi) != ESP_OK) return false;

  expanderSet(EXIO_LCD_CS, false);
  delay(10);
  for (size_t i = 0; i < sizeof ST7701_INIT;) {
    uint8_t cmd = ST7701_INIT[i++], n = ST7701_INIT[i++];
    st7701Send(false, cmd);
    if (n == DELAY_120) {
      delay(120);
      continue;
    }
    for (int j = 0; j < n; j++) st7701Send(true, ST7701_INIT[i++]);
  }
  expanderSet(EXIO_LCD_CS, true);
  delay(10);
  spi_bus_remove_device(lcdSpi);
  spi_bus_free(SPI2_HOST);    // GPIO 1 and 2 are also the SD card's, free for it now
  return true;
}

// --- the RGB panel and its frame buffers ---------------------------------------------------------
static esp_lcd_panel_handle_t panel;
static uint16_t *fbs[3];
static int fbCount, bounceLines;

// Which frame buffer is on screen and which is queued to replace it. Changed only on core 0:
// queueFrame (a task) and the panel's interrupts, with interrupts off while queueFrame runs, so the
// driver's own switch and these two can't get out of step.
static portMUX_TYPE swapLock = portMUX_INITIALIZER_UNLOCKED;
static volatile int fbShown = 0, fbQueued = 0;
static int vsyncsSinceQueue;          // only touched with swapLock held
static volatile uint32_t vsyncCount, swapCount;
static TaskHandle_t swapWaiter;   // the task waiting in nextBackBuffer

// Bounce-buffer mode: called when the driver has finished copying a frame into the bounce buffers
// and has switched to the queued frame buffer for the next one.
static bool IRAM_ATTR onFrameDone(esp_lcd_panel_handle_t, const esp_lcd_rgb_panel_event_data_t *, void *) {
  portENTER_CRITICAL_ISR(&swapLock);
  bool changed = fbShown != fbQueued;
  fbShown = fbQueued;
  portEXIT_CRITICAL_ISR(&swapLock);
  BaseType_t woke = pdFALSE;
  if (changed) {
    swapCount = swapCount + 1;
    if (swapWaiter) vTaskNotifyGiveFromISR(swapWaiter, &woke);
  }
  return woke == pdTRUE;
}

// Every vsync. Without bounce buffers the screen's DMA reads the frame buffer itself and may have
// fetched ahead, so a queued frame counts as shown only after two vsyncs.
static bool IRAM_ATTR onVsync(esp_lcd_panel_handle_t, const esp_lcd_rgb_panel_event_data_t *, void *) {
  vsyncCount = vsyncCount + 1;
  if (bounceLines) return false;
  bool changed = false;
  portENTER_CRITICAL_ISR(&swapLock);
  if (fbShown != fbQueued && ++vsyncsSinceQueue >= 2) {
    fbShown = fbQueued;
    changed = true;
  }
  portEXIT_CRITICAL_ISR(&swapLock);
  BaseType_t woke = pdFALSE;
  if (changed) {
    swapCount = swapCount + 1;
    if (swapWaiter) vTaskNotifyGiveFromISR(swapWaiter, &woke);
  }
  return woke == pdTRUE;
}

// Creates and starts the panel. Run it on core 0 (see the top of this file).
static esp_err_t panelCreate(int pclkMHz, int bounce, int numFbs) {
  bounceLines = bounce;
  fbCount = numFbs;
  esp_lcd_rgb_panel_config_t cfg;
  memset(&cfg, 0, sizeof cfg);
  cfg.clk_src = LCD_CLK_SRC_PLL240M;            // 240 MHz divides evenly into 10, 12, 16, 20 and 30
  cfg.timings.pclk_hz = pclkMHz * 1000000;
  cfg.timings.h_res = LCD_W;
  cfg.timings.v_res = LCD_H;
  cfg.timings.hsync_pulse_width = H_PULSE;
  cfg.timings.hsync_back_porch = H_BACK;
  cfg.timings.hsync_front_porch = H_FRONT;
  cfg.timings.vsync_pulse_width = V_PULSE;
  cfg.timings.vsync_back_porch = V_BACK;
  cfg.timings.vsync_front_porch = V_FRONT;
  cfg.data_width = 16;
  cfg.bits_per_pixel = 16;
  cfg.num_fbs = numFbs;
  cfg.bounce_buffer_size_px = bounce * LCD_W;
  cfg.dma_burst_size = 64;
  cfg.hsync_gpio_num = PIN_HSYNC;
  cfg.vsync_gpio_num = PIN_VSYNC;
  cfg.de_gpio_num = PIN_DE;
  cfg.pclk_gpio_num = PIN_PCLK;
  cfg.disp_gpio_num = -1;
  for (int i = 0; i < 16; i++) cfg.data_gpio_nums[i] = PIN_DATA[i];
  cfg.flags.fb_in_psram = 1;
  esp_err_t err = esp_lcd_new_rgb_panel(&cfg, &panel);
  if (err != ESP_OK) return err;

  esp_lcd_rgb_panel_event_callbacks_t cbs;
  memset(&cbs, 0, sizeof cbs);
  cbs.on_vsync = onVsync;
  if (bounce) cbs.on_frame_buf_complete = onFrameDone;
  esp_lcd_rgb_panel_register_event_callbacks(panel, &cbs, NULL);
  esp_lcd_panel_reset(panel);
  if ((err = esp_lcd_panel_init(panel)) != ESP_OK) return err;

  void *f[3] = {NULL, NULL, NULL};
  if (numFbs == 3) esp_lcd_rgb_panel_get_frame_buffer(panel, 3, &f[0], &f[1], &f[2]);
  else if (numFbs == 2) esp_lcd_rgb_panel_get_frame_buffer(panel, 2, &f[0], &f[1]);
  else esp_lcd_rgb_panel_get_frame_buffer(panel, 1, &f[0]);
  for (int i = 0; i < 3; i++) fbs[i] = (uint16_t *)f[i];
  return ESP_OK;
}

// Puts frame buffer i on screen from the next frame. Run it on core 0.
static void queueFrame(int i) {
  if (bounceLines) {
    // In bounce-buffer mode this only records the new buffer; the driver switches to it when it
    // starts copying the next frame. Interrupts are off on this core meanwhile, so onFrameDone sees
    // the driver's choice and fbQueued agree.
    portENTER_CRITICAL(&swapLock);
    esp_lcd_panel_draw_bitmap(panel, 0, 0, LCD_W, LCD_H, fbs[i]);
    fbQueued = i;
    portEXIT_CRITICAL(&swapLock);
  } else {
    // Without bounce buffers this also writes the CPU's cached pixels out to PSRAM, where the DMA
    // reads them, so it runs with interrupts on.
    esp_lcd_panel_draw_bitmap(panel, 0, 0, LCD_W, LCD_H, fbs[i]);
    portENTER_CRITICAL(&swapLock);
    fbQueued = i;
    vsyncsSinceQueue = 0;
    portEXIT_CRITICAL(&swapLock);
  }
}

// A frame buffer that's neither on screen nor queued: free to draw into. Waits for one if needed.
static int nextBackBuffer() {
  swapWaiter = xTaskGetCurrentTaskHandle();
  for (;;) {
    int shown = fbShown, queued = fbQueued;
    for (int i = 0; i < fbCount; i++)
      if (i != shown && i != queued) return i;
    ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(100));
  }
}

// Fills every frame buffer with black (the area outside the round window stays black from here on).
static void clearFrameBuffers() {
  for (int i = 0; i < fbCount; i++) {
    memset(fbs[i], 0, LCD_W * LCD_H * 2);
    esp_cache_msync(fbs[i], LCD_W * LCD_H * 2, ESP_CACHE_MSYNC_FLAG_DIR_C2M | ESP_CACHE_MSYNC_FLAG_UNALIGNED);
  }
}

// --- the helper task on core 0 -------------------------------------------------------------------
typedef void (*CoreJob)(void *arg);
static TaskHandle_t core0Task;
static SemaphoreHandle_t core0Done;
static CoreJob core0Job;
static void *core0Arg;

static void core0Loop(void *) {
  for (;;) {
    ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
    core0Job(core0Arg);
    xSemaphoreGive(core0Done);
  }
}

static void core0Start() {
  core0Done = xSemaphoreCreateBinary();
  xTaskCreatePinnedToCore(core0Loop, "core0", 6144, NULL, 10, &core0Task, 0);
}

// Starts job(arg) on core 0 and returns at once; waitCore0() waits for it to finish.
static void startOnCore0(CoreJob job, void *arg) {
  core0Job = job;
  core0Arg = arg;
  xTaskNotifyGive(core0Task);
}

static void waitCore0() { xSemaphoreTake(core0Done, portMAX_DELAY); }

static void runOnCore0(CoreJob job, void *arg) {
  startOnCore0(job, arg);
  waitCore0();
}
