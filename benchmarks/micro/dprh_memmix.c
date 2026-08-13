#define _POSIX_C_SOURCE 200112L

  #include <errno.h>
  #include <inttypes.h>
  #include <stdint.h>
  #include <stdio.h>
  #include <stdlib.h>
  #include <string.h>

  enum {
      CACHE_LINE = 64,
      GATHER_LANES = 8,
  };

  typedef struct {
      uint64_t value;
      unsigned char padding[CACHE_LINE - sizeof(uint64_t)];
  } CacheLine;

  _Static_assert(sizeof(CacheLine) == CACHE_LINE,
                 "CacheLine must occupy exactly one cache line");

  static volatile uint64_t final_sink;
  static uint64_t rng_state;

  static void
  fail(const char *message)
  {
      fprintf(stderr, "dprh_memmix: %s\n", message);
      exit(2);
  }

  static uint64_t
  parse_u64(const char *text)
  {
      char *end = NULL;
      errno = 0;
      unsigned long long value = strtoull(text, &end, 10);

      if (errno != 0 || end == text || *end != '\0')
          fail("invalid numeric argument");

      return (uint64_t)value;
  }

  static void *
  allocate_aligned(size_t bytes)
  {
      void *ptr = NULL;

      if (posix_memalign(&ptr, 4096, bytes) != 0 || ptr == NULL)
          fail("aligned allocation failed");

      memset(ptr, 0, bytes);
      return ptr;
  }

  static uint64_t
  next_random(void)
  {
      uint64_t x = rng_state;

      x ^= x >> 12;
      x ^= x << 25;
      x ^= x >> 27;
      rng_state = x;

      return x * UINT64_C(2685821657736338717);
  }

  static void
  shuffle_indices(uint32_t *order, size_t count)
  {
      for (size_t i = 0; i < count; ++i)
          order[i] = (uint32_t)i;

      for (size_t i = count - 1; i > 0; --i) {
          size_t j = (size_t)(next_random() % (i + 1));
          uint32_t tmp = order[i];
          order[i] = order[j];
          order[j] = tmp;
      }
  }

  static uint64_t
  run_stream(const volatile CacheLine *stream, size_t stream_lines,
             unsigned sequential_per_batch, uint64_t batches)
  {
      uint64_t accumulator = 0;
      size_t position = 0;

      for (uint64_t batch = 0; batch < batches; ++batch) {
          for (unsigned i = 0; i < sequential_per_batch; ++i) {
              accumulator += stream[position].value;
              if (++position == stream_lines)
                  position = 0;
          }
      }

      return accumulator;
  }

  static uint64_t
  run_gather(const volatile CacheLine *values,
             const volatile uint32_t *order, size_t value_lines,
             uint64_t batches)
  {
      uint64_t accumulators[GATHER_LANES] = {0};
      size_t position = 0;

      for (uint64_t batch = 0; batch < batches; ++batch) {
          for (unsigned lane = 0; lane < GATHER_LANES; ++lane) {
              uint32_t index = order[position];
              if (++position == value_lines)
                  position = 0;

              accumulators[lane] += values[index].value;
          }
      }

      uint64_t total = 0;
      for (unsigned lane = 0; lane < GATHER_LANES; ++lane)
          total += accumulators[lane];

      return total;
  }

  static uint64_t
  run_mixed(const volatile CacheLine *stream, size_t stream_lines,
            const volatile CacheLine *values,
            const volatile uint32_t *order, size_t value_lines,
            unsigned sequential_per_batch, uint64_t batches)
  {
      uint64_t sequential_accumulator = 0;
      uint64_t gather_accumulators[GATHER_LANES] = {0};
      size_t stream_position = 0;
      size_t gather_position = 0;

      for (uint64_t batch = 0; batch < batches; ++batch) {
          for (unsigned i = 0; i < sequential_per_batch; ++i) {
              sequential_accumulator += stream[stream_position].value;
              if (++stream_position == stream_lines)
                  stream_position = 0;
          }

          for (unsigned lane = 0; lane < GATHER_LANES; ++lane) {
              uint32_t index = order[gather_position];
              if (++gather_position == value_lines)
                  gather_position = 0;

              gather_accumulators[lane] += values[index].value;
          }
      }

      for (unsigned lane = 0; lane < GATHER_LANES; ++lane)
          sequential_accumulator += gather_accumulators[lane];

      return sequential_accumulator;
  }

  int
  main(int argc, char **argv)
  {
      if (argc != 7) {
          fprintf(
              stderr,
              "usage: %s MODE STREAM_MIB RANDOM_MIB "
              "SEQUENTIAL_PER_BATCH BATCHES SEED\n",
              argv[0]
          );
          return 2;
      }

      const char *mode = argv[1];
      uint64_t stream_mib = parse_u64(argv[2]);
      uint64_t random_mib = parse_u64(argv[3]);
      uint64_t sequential_per_batch_u64 = parse_u64(argv[4]);
      uint64_t batches = parse_u64(argv[5]);
      uint64_t seed = parse_u64(argv[6]);

      int use_stream =
          strcmp(mode, "stream") == 0 || strcmp(mode, "mixed") == 0;
      int use_gather =
          strcmp(mode, "gather") == 0 || strcmp(mode, "mixed") == 0;

      if (!use_stream && !use_gather)
          fail("MODE must be stream, gather, or mixed");

      if (use_stream && stream_mib == 0)
          fail("stream mode requires STREAM_MIB > 0");

      if (use_gather && random_mib == 0)
          fail("gather mode requires RANDOM_MIB > 0");

      if (sequential_per_batch_u64 > 64)
          fail("SEQUENTIAL_PER_BATCH must be <= 64");

      if (use_stream && sequential_per_batch_u64 == 0)
          fail("stream mode requires SEQUENTIAL_PER_BATCH > 0");

      if (batches == 0 || seed == 0)
          fail("BATCHES and SEED must be nonzero");

      if (stream_mib > 512 || random_mib > 512)
          fail("each working set must be <= 512 MiB");

      unsigned sequential_per_batch =
          (unsigned)sequential_per_batch_u64;

      size_t stream_lines =
          (size_t)(stream_mib * 1024 * 1024 / CACHE_LINE);
      size_t value_lines =
          (size_t)(random_mib * 1024 * 1024 / CACHE_LINE);

      if (value_lines > UINT32_MAX)
          fail("random working set is too large for 32-bit indices");

      CacheLine *stream = NULL;
      CacheLine *values = NULL;
      uint32_t *order = NULL;

      rng_state = seed;

      if (use_stream) {
          stream = allocate_aligned(stream_lines * sizeof(*stream));
          for (size_t i = 0; i < stream_lines; ++i)
              stream[i].value = (uint64_t)i + seed;
      }

      if (use_gather) {
          values = allocate_aligned(value_lines * sizeof(*values));
          order = allocate_aligned(value_lines * sizeof(*order));

          for (size_t i = 0; i < value_lines; ++i)
              values[i].value =
                  ((uint64_t)i * UINT64_C(11400714819323198485)) ^ seed;

          shuffle_indices(order, value_lines);
      }

      printf(
          "[dprh-micro] kernel begin mode=%s stream_mib=%" PRIu64
          " random_mib=%" PRIu64 " seq_per_batch=%u"
          " batches=%" PRIu64 " seed=%" PRIu64 "\n",
          mode, stream_mib, random_mib, sequential_per_batch,
          batches, seed
      );
      fflush(stdout);

      uint64_t result;

      if (strcmp(mode, "stream") == 0) {
          result = run_stream(
              stream, stream_lines, sequential_per_batch, batches
          );
      } else if (strcmp(mode, "gather") == 0) {
          result = run_gather(values, order, value_lines, batches);
      } else {
          result = run_mixed(
              stream, stream_lines, values, order, value_lines,
              sequential_per_batch, batches
          );
      }

      final_sink = result;
      printf("[dprh-micro] complete sink=%" PRIu64 "\n", result);

      free(order);
      free(values);
      free(stream);
      return 0;
  }
