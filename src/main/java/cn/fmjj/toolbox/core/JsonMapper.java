package cn.fmjj.toolbox.core;

import com.fasterxml.jackson.annotation.JsonAutoDetect;
import com.fasterxml.jackson.annotation.PropertyAccessor;
import com.fasterxml.jackson.databind.ObjectMapper;

/**
 * 全局共享 ObjectMapper：字段级可见性 + 非私有访问器全开，
 * 免去每个配置类都写 @JsonProperty（JavaBean 的 getXxx 命名对本项目太啰嗦）。
 */
public final class JsonMapper {

    private static final ObjectMapper INSTANCE = new ObjectMapper();

    static {
        INSTANCE.setVisibility(PropertyAccessor.FIELD, JsonAutoDetect.Visibility.ANY);
        INSTANCE.setVisibility(PropertyAccessor.GETTER, JsonAutoDetect.Visibility.NON_PRIVATE);
        INSTANCE.setVisibility(PropertyAccessor.IS_GETTER, JsonAutoDetect.Visibility.NON_PRIVATE);
        INSTANCE.setVisibility(PropertyAccessor.SETTER, JsonAutoDetect.Visibility.NON_PRIVATE);
        INSTANCE.setVisibility(PropertyAccessor.CREATOR, JsonAutoDetect.Visibility.ANY);
    }

    private JsonMapper() {}

    public static ObjectMapper get() { return INSTANCE; }
}
